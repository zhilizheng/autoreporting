#! /usr/bin/python3

import argparse,shlex,subprocess, glob, re
from subprocess import Popen, PIPE
import pandas as pd
import numpy as np
from autoreporting_utils import *
import gwcatalog_api
import custom_catalog
import os
from multiprocessing.dummy import Pool as ThreadPool
from typing import Dict

def map_alleles(a1,a2):
    """
    Flips alleles to the A strand if necessary and orders them lexicogaphically
    Author: Pietro, with small changes by Arto
    """
    allele_dict={"T":"A","C":"G","G":"C"}
     # check if the A variant is present
    if 'A' not in a1 + a2 and 'a' not in a1+a2:
        # for both/ref and alt map them to the A strand and order each one lexicographically
        a1 = ''.join([allele_dict[elem.upper()] for elem in a1])
        a2 = ''.join([allele_dict[elem.upper()] for elem in a2])
    # further sorting
    return sorted([a1,a2])

def map_column(df,col_name,columns):
    df_=df.copy()
    if df_.empty:
        cols=list(df_.columns)
        cols.append(col_name)
        return pd.DataFrame(columns=cols)
    df_[["temp_map_ref","temp_map_alt"]]=df_.loc[:,[columns["ref"],columns["alt"] ]].apply(lambda x: map_alleles(*x),axis=1,result_type="expand")
    df_[col_name]=create_variant_column(df_,chrom=columns["chrom"],pos=columns["pos"],ref="temp_map_ref",alt="temp_map_alt")
    df_=df_.drop(columns=["temp_map_ref","temp_map_alt"])
    return df_

def indel_helper(row, chrom,pos,ref,alt):
    row["ref"] = ref
    row["alt"] = alt
    row["chrom"] = chrom
    row["pos"] = pos
    return row

def solve_indels(indel_df,df,columns):
    """ Solve exact matches for indels where they are missing one basepair 
    """
    out_df=pd.DataFrame(columns=indel_df.columns)
    for _, row in indel_df.iterrows():
        #check if our dataframe has an exact match.
        rowpos=int(row["pos"])-1
        rowchrom=str(row["chrom"])
        possible_matches=df.loc[(df[columns["chrom"]].astype(str) == rowchrom)&(df[columns["pos"]] == rowpos )  ,:].copy()
        for __, row2 in possible_matches.iterrows():
            #if alleles are a match s.t. -/TCGA == T/TTCGA, add that to output df and break from the loop
            a1=row["ref"]
            a2=row["alt"]
            b1=row2[columns["ref"]]
            b2=row2[columns["alt"]]
            n_row=row.copy()
            if a1=="-":
                if len(b1)==1 and b2[1:] == a2:
                    n_row=indel_helper(n_row,row2[columns["chrom"]],row2[columns["pos"]],b1,b2)
                    out_df=out_df.append(n_row,sort=True)
                    break
                elif len(b2)==1 and b1[1:] == a2:
                    n_row=indel_helper(n_row,row2[columns["chrom"]],row2[columns["pos"]],b2,b1)
                    out_df=out_df.append(n_row,sort=True)
                    break
            elif a2=="-":
                if len(b1)==1 and b2[1:] == a1:
                    n_row=indel_helper(n_row,row2[columns["chrom"]],row2[columns["pos"]],b2,b1)
                    out_df=out_df.append(n_row,sort=True)
                    break
                elif len(b2)==1 and b1[1:] == a1:
                    n_row=indel_helper(n_row,row2[columns["chrom"]],row2[columns["pos"]],b1,b2)
                    out_df=out_df.append(n_row,sort=True)
                    break
            #else, continue
    return out_df

def create_top_level_report(report_df,efo_traits,columns,grouping_method,significance_threshold,strict_ld_threshold):
    """
    Create a top level report from which it is easy to see which loci are novel
    In: report_out df, traits that appear in matching_pheno_gwas_catalog_hits, column names
    Out: Dataframe with a row for every lead variant in df, with columns locus_id chr, start, end, matching_pheno_gwas_catalog_hits other_gwas_hits  
    """ 
    top_level_columns=["locus_id",
                        "chr",
                        "start",
                        "end",
                        "enrichment",
                        "most_severe_gene",
                        "most_severe_consequence",
                        "lead_pval",
                        "lead_beta",
                        "lead_AF",
                        "lead_AF_cases",
                        "lead_AF_controls",
                        "found_associations_strict",
                        "found_associations_relaxed",
                        "credible_set_variants",
                        "functional_variants_strict",
                        "functional_variants_relaxed"]
    if efo_traits:
        top_level_columns.append("specific_efo_trait_associations_strict")
        top_level_columns.append("specific_efo_trait_associations_relaxed")
    
    df=report_df.copy()
    top_level_df=pd.DataFrame(columns=top_level_columns)

    if df.empty:
        return top_level_df
    
    list_of_loci=list(df["locus_id"].unique())
    
    for locus_id in list_of_loci:
        # The row is a dict which will contain the aggregated values for a single group
        row = {}
        # Separate group variants from all variants
        loc_variants=df.loc[df["locus_id"]==locus_id,:]
        # Create strict group. The definition changes based on the grouping method.
        strict_group=None
        if grouping_method == "cred":
            strict_group = loc_variants[~loc_variants["cs_id"].isna()].copy()
        elif grouping_method == "ld":
            pass
            strict_group = loc_variants[(loc_variants[columns["pval"]]<=significance_threshold) & (loc_variants["r2_to_lead"]>=strict_ld_threshold )].copy()
        else:
            strict_group = loc_variants[loc_variants[columns["pval"]]<=significance_threshold].copy()
        
        row['locus_id']=locus_id
        row["chr"]=loc_variants[columns["chrom"]].iat[0]
        row["start"]=np.amin(loc_variants[columns["pos"]])
        row["end"]=np.amax(loc_variants[columns["pos"]])
        # Add annotation info. Try because it is possible that annotation step was skipped.
        try:
            enrichment=loc_variants.loc[loc_variants["#variant"]==locus_id,"GENOME_FI_enrichment_nfe_est"].iat[0]
            most_severe_gene=loc_variants.loc[loc_variants["#variant"]==locus_id,"most_severe_gene"].iat[0]
            most_severe_consequence=loc_variants.loc[loc_variants["#variant"]==locus_id,"most_severe_consequence"].iat[0]
        except:
            enrichment=""
            most_severe_gene=""
            most_severe_consequence=""
        row["enrichment"]=enrichment
        row["most_severe_consequence"]=most_severe_consequence
        row["most_severe_gene"]=most_severe_gene
        pvalue=loc_variants.loc[loc_variants["#variant"]==locus_id,columns["pval"]].values[0]
        beta=loc_variants.loc[loc_variants["#variant"]==locus_id,columns["beta"]].values[0]
        af=loc_variants.loc[loc_variants["#variant"]==locus_id,columns["af"]].values[0]
        af_case=loc_variants.loc[loc_variants["#variant"]==locus_id,columns["af_cases"]].values[0]
        af_control=loc_variants.loc[loc_variants["#variant"]==locus_id,columns["af_controls"]].values[0]
        row["lead_pval"]=pvalue
        row["lead_beta"]=beta
        row["lead_AF"]=af
        row["lead_AF_cases"]=af_case
        row["lead_AF_controls"]=af_control
        # Get credible set variants in relazed & strict group, as well as functional variants. 
        # Try because it is possible that functional data was skipped.
        cred_s = loc_variants.loc[~loc_variants["cs_id"].isna(),["#variant","cs_prob"] ].drop_duplicates()
        cred_set=";".join( "{}|{:.3g}".format(t._1,t.cs_prob) for t in  cred_s.itertuples() )
        try:
            func_s = loc_variants.loc[~loc_variants["functional_category"].isna(),["#variant","functional_category","r2_to_lead"] ].drop_duplicates()
            func_set=";".join("{}|{}|{:.3g}".format(t._1,t.functional_category,t.r2_to_lead) for t in  func_s.itertuples())
            func_s_strict = strict_group.loc[~strict_group["functional_category"].isna(),["#variant","functional_category"] ].drop_duplicates()
            func_set_strict=";".join("{}|{}".format(t._1,t.functional_category) for t in  func_s_strict.itertuples())
        except:
            func_set=""
            func_set_strict=""
        row["credible_set_variants"]=cred_set
        row["functional_variants_strict"]=func_set_strict
        row["functional_variants_relaxed"]=func_set
        # Solve traits for relaxed and strict groups.
        all_traits=(
                    loc_variants[["trait","trait_name","r2_to_lead"]]
                    .sort_values(by="r2_to_lead",ascending=False)
                    .drop_duplicates(subset=["trait","trait_name"], keep="first")
                    .dropna()
                    )
        strict_traits=(
                    strict_group[["trait","trait_name","r2_to_lead"]]
                    .sort_values(by="r2_to_lead",ascending=False)
                    .drop_duplicates(subset=["trait","trait_name"],keep="first")
                    .dropna()
                    )
        
        other_traits_relaxed = all_traits[ ~all_traits["trait"].isin(efo_traits) ].copy()
        other_traits_strict = strict_traits[ ~strict_traits["trait"].isin(efo_traits) ].copy()

        row["found_associations_relaxed"]=";".join( "{}|{:.3g}".format(t.trait_name,t.r2_to_lead) for t in other_traits_relaxed.itertuples() )
        row["found_associations_strict"]=";".join( "{}|{:.3g}".format(t.trait_name,t.r2_to_lead) for t in other_traits_strict.itertuples() )
        
        if efo_traits:
            matching_traits_relaxed= all_traits[ all_traits["trait"].isin(efo_traits) ].copy()
            matching_traits_strict = strict_traits[ strict_traits["trait"].isin(efo_traits) ].copy()
            row["specific_efo_trait_associations_relaxed"]=";".join( "{}|{:.3g}".format(t.trait_name,t.r2_to_lead) for t in matching_traits_relaxed.itertuples() )
            row["specific_efo_trait_associations_strict"]=";".join( "{}|{:.3g}".format(t.trait_name,t.r2_to_lead) for t in matching_traits_strict.itertuples() )
        top_level_df=top_level_df.append(row,ignore_index=True)

    return top_level_df

    
def load_api_summaries(df, gwascatalog_pad, gwascatalog_pval,gwapi,gwascatalog_threads, columns):
    range_df=df.loc[:,[columns["chrom"],columns["pos"] ]].copy(deep=True)
    range_df.loc[:,"pos2"]=range_df.loc[:,columns["pos"] ]
    range_df=range_df.rename(columns={columns["pos"]:"pos_rmin","pos2":"pos_rmax"})

    pad=gwascatalog_pad*1000
    range_df.loc[:,"pos_rmin"]=range_df.loc[:,"pos_rmin"]-pad
    range_df.loc[:,"pos_rmax"]=range_df.loc[:,"pos_rmax"]+pad
    range_df.loc[:,"pos_rmin"]=range_df.loc[:,"pos_rmin"].clip(lower=0)
    regions=prune_regions(range_df,columns=columns)
    #use api to get all gwascatalog hits
    data_lst=[]
    for _,region in regions.iterrows():
        data_lst.append([region[ columns["chrom"] ], region["min"],region["max"],gwascatalog_pval])
    r_lst=None
    threads=gwascatalog_threads
    with ThreadPool(threads) as pool:
        r_lst=pool.starmap(gwapi.get_associations,data_lst)
    #remove empties, flatten
    r_lst=[r for r in r_lst if r != None]
    result_lst=[i for sublist in r_lst for i in sublist]
    gwas_df=pd.DataFrame(result_lst)
    #resolve indels
    if gwas_df.empty:
        return pd.DataFrame(columns=["chrom","pos","ref","alt","pval","pval_mlog","trait","trait_name","code","study","study_link"])
    indel_idx=(gwas_df["ref"]=="-")|(gwas_df["alt"]=="-")
    indels=solve_indels(gwas_df.loc[indel_idx,:],df,columns)
    gwas_df=gwas_df.loc[~indel_idx,:]
    gwas_df=pd.concat([gwas_df,indels],sort=True).reset_index(drop=True)
    return gwas_df

def load_custom_dataresource(df: pd.DataFrame, gwascatalog_pad: int, gwascatalog_pval: float, resourceapi: custom_catalog.CustomCatalog, columns: Dict[str, str]) -> pd.DataFrame :
    """Load variants from custom dataresource
    Load variants corresponding to the filtered variants from a custom dataresource, i.e. a CustomCatalog.

    Args:
        df (pd.DataFrame): Input dataframe
        gwascatalog_pad (int): Range around group to search for variants for
        gwascatalog_pval (float): P-value threshold
        resourceapi (custom_catalog.CustomCatalog): The catalog to get associations from
        columns (Dict[str, str]): Column names for df
    Returns:
        pd.DataFrame: A dataframe containing the variants that are in roughly the same region as filtered variants. 
    """
    #make regions based on pos_rmin, pos_rmax
    range_df = df.loc[:,[columns["chrom"],"pos_rmin","pos_rmax"]].drop_duplicates().copy(deep=True)
    regions = prune_regions(range_df,columns)
    outputs=[]
    for _, region in regions.iterrows():
        outputs.append(resourceapi.get_associations(region[columns["chrom"]], region["min"],region["max"],gwascatalog_pval))
    outputs=[r for r in outputs if r != None]
    outputs=[i for sublist in outputs for i in sublist]
    customresource_df = pd.DataFrame(outputs)
    if customresource_df.empty:
        return pd.DataFrame(columns=["chrom","pos","ref","alt","pval","beta","se","trait","trait_name","study_doi"])
    customresource_df = filter_invalid_alleles(customresource_df,columns)
    customresource_df["trait_name"] = customresource_df["trait"]
    return customresource_df

def extract_ld_variants(df,summary_df,locus,ldstore_threads,ld_treshold,prefix,columns):
    if df.loc[df["locus_id"]==locus,"pos_rmax"].shape[0]<=1:
        return
    chromosome=df.loc[df["#variant"]==locus,columns["chrom"] ].unique()[0]
    print("Chromosome {}, group {} ld computation, variant amount {}".format(chromosome,locus,df.loc[df["locus_id"]==locus,"pos_rmax"].shape[0]))
    #get group range
    r_max=df.loc[df["locus_id"]==locus,"pos_rmax"].values[0]
    r_min=df.loc[df["locus_id"]==locus,"pos_rmin"].values[0]
    if r_max == r_min:
        return
    #calculate ld for that group
    threads=min(ldstore_threads,df.loc[df["locus_id"]==locus,"pos_rmax"].shape[0]) # ldstore silently errors out when #variants < #threads, do this to alleviate that 
    ldstore_command="ldstore --bplink {}temp_chrom --bcor {}temp_corr.bcor --ld-thold {}  --incl-range {}-{} --n-threads {}".format(
        prefix, prefix, ld_treshold**0.5, r_min, r_max, threads)
    pr = subprocess.run(shlex.split(ldstore_command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='ASCII' )
    if pr.returncode!=0:
        print("LDSTORE FAILURE for locus {}".format(locus)  )
        print(pr.stdout)
        return
    #merge the file
    ldstore_merge_command="ldstore --bcor {}temp_corr.bcor --merge {}".format(prefix, threads)
    pr = subprocess.run(shlex.split(ldstore_merge_command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='ASCII' )
    if pr.returncode!=0:
        print("LDSTORE FAILURE for locus {}".format(locus)  )
        print(pr.stdout)
        return
    #check if ldstore merged file exists, if not, throw an error
    if not os.path.exists("{}temp_corr.bcor".format(prefix)):
        raise FileNotFoundError("The LD correlation file {} does not exist. Check that the chromosome index is correct, e.g. 23 in both summary statistic and LD panel instead of 23 and X.".format(prefix+"temp_corr.bcor"))
    #create list of variants of interest.
    var_cols=["#variant",columns["pos"],columns["chrom"],columns["ref"],columns["alt"]]
    var_rename={"#variant":"RSID",columns["pos"]:"position",columns["chrom"]:"chromosome",columns["ref"]:"A_allele",columns["alt"]:"B_allele"}
    extract_df_1=df.loc[df["locus_id"]==locus,:].copy()
    extract_df_2=summary_df.loc[(summary_df[columns["pos"]] <=r_max) & (summary_df[columns["pos"]] >=r_min)  ,:].copy()
    extract_df=pd.concat([extract_df_1,extract_df_2],sort=True).loc[:,var_cols].rename(columns=var_rename).drop_duplicates().sort_values(by="position")
    ### different datatypes caused drop duplicates to not recognize duplicates and ldstore failed in the next step
    extract_df = extract_df.astype(str)
    extract_df = extract_df.drop_duplicates()
    var_lst_name="{}var_lst".format(prefix)
    extract_df.to_csv(var_lst_name,sep=" ",index=False)
    #extract variants of interest 
    ldstore_extract_command="ldstore --bcor {}temp_corr.bcor --table {}ld_table.table --incl-variants {}".format(prefix, prefix, var_lst_name)
    pr = subprocess.run(shlex.split(ldstore_extract_command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='ASCII' )
    if pr.returncode!=0:
        print("LDSTORE FAILURE for locus {}".format(locus)  )
        print(pr.stdout)
        return
    #read ld_table, make it so rsid1 is for our variants and rsid2 for summary variants
    if not os.path.exists("{}ld_table.table".format(prefix)):
        raise FileNotFoundError("The LD association table {} does not exist. Check that the chromosome index is correct, e.g. 23 in both summary statistic and LD panel instead of 23 and X.".format(args.prefix+"ld_table.table"))
    ld_table=pd.read_csv("{}ld_table.table".format(prefix),sep="\s+").loc[:,["chromosome","RSID1","RSID2","correlation"]]
    if ld_table.empty:
        return
    ld_table2=ld_table.copy()
    ld_table2=ld_table2.rename(columns={"RSID1":"RSID2","RSID2":"RSID1"})
    ld=pd.concat([ld_table,ld_table2],sort=True).reset_index(drop=True)
    ld[columns["chrom"]]=ld["RSID2"].apply(lambda x:x.strip("chr").split("_")[0] )
    ld[columns["pos"]]=ld["RSID2"].apply(lambda x:x.strip("chr").split("_")[1] )
    ld[columns["ref"]]=ld["RSID2"].apply(lambda x:x.strip("chr").split("_")[2] )
    ld[columns["alt"]]=ld["RSID2"].apply(lambda x:x.strip("chr").split("_")[3] )
    #filter
    ld=ld.merge(extract_df_1.loc[:,["#variant"] ].rename(columns={"#variant":"RSID1"}),how="inner",on="RSID1")
    ld=map_column(ld,"RSID2_map",columns)
    #is #variant mapped to A strand? maybe, but this should be checked.
    ld=ld.merge(extract_df_2.loc[:,["#variant"] ].rename(columns={"#variant":"RSID2_map"}),how="inner",on="RSID2_map")
    ld.loc[:,"r2"]=ld["correlation"]*ld["correlation"]
    ld=ld.drop(columns=["correlation",columns["chrom"],columns["pos"],columns["ref"],columns["alt"] ])
    #remove temporary files
    corr_files=glob.glob("{}temp_corr.*".format(prefix))
    rmcmd="rm {}ld_table.table {}var_lst".format(prefix, prefix, prefix)
    Popen(shlex.split(rmcmd)+corr_files,stderr=subprocess.DEVNULL,stdout=subprocess.DEVNULL)
    return ld

def filter_invalid_alleles(df: pd.DataFrame,columns: Dict[str, str]) -> pd.DataFrame :
    """Filter alleles that do not have ACGT in them out
    Args:
        df (pd.DataFrame): Input dataframe
        columns (Dict[str,str]): column names
    Returns:
        pd.DataFrame: dataframe with invalid variants removed 
    """
    mset='^[acgtACGT]+$'
    matchset1=df[columns["ref"]].apply(lambda x:bool(re.match(mset,x)))
    matchset2=df[columns["ref"]].apply(lambda x:bool(re.match(mset,x)))
    retval = df[matchset1 & matchset2].copy()
    return retval

def compare(df, compare_style, ld_check, plink_mem, ld_panel_path,
            prefix, gwascatalog_pval, gwascatalog_pad, gwascatalog_threads,
            ldstore_threads, ld_treshold, cache_gwas, columns, 
            gwapi, customdataresource_path):
    """
    Compares found significant variants to gwascatalog results and/or supplied summary statistic files
    In: df, compare_style, summary_fpath, endpoints, ld_check, plink_mem, ld_panel_path,
        prefix, report_out, ld_report_out, gwascatalog_pval, gwascatalog_pad, gwascatalog_threads,
        ldstore_threads, ld_treshold, cache_gwas, columns, gwapi
    Out: A tuple (report_df, ld_df)
        report_df: the dataframe containing all of the variants and their previous associations, as well as annotations
        ld_df (optional): a dataframe containing the LD paired associations of variants
        
    """
    if df.empty:
        #print("No variants, {} and {} will not be produced".format(report_out, ld_report_out))
        return (None, None)
    necessary_columns=[ columns["chrom"],columns["pos"],columns["ref"],columns["alt"],columns["pval"],"#variant","locus_id","pos_rmin","pos_rmax"]
    df_cols=df.columns.to_list()
    if not all(nec_col in df_cols for nec_col in necessary_columns):
        Exception("GWS variant file {} did not contain all of the necessary columns:\n{} ".format(compare_fname,necessary_columns))
    summary_df_1=pd.DataFrame()
    summary_df_2=pd.DataFrame()
    #building summaries, external files and/or gwascatalog
    if compare_style in ["file","both"]:
        summary_df_1=load_custom_dataresource(df.copy(), gwascatalog_pad, gwascatalog_pval, customdataresource,columns)
        rename_dict={"chrom":columns["chrom"],"pos":columns["pos"],"ref":columns["ref"],"alt":columns["alt"],"pval":columns["pval"]}
        summary_df_1=summary_df_1.rename(columns=rename_dict)
        summary_df_1.loc[:,"#variant"]=create_variant_column(summary_df_1,chrom=columns["chrom"],pos=columns["pos"],ref=columns["ref"],alt=columns["alt"])
    if compare_style in ["gwascatalog","both"]:
        gwas_df=None
        if os.path.exists("{}gwas_out_mapping.tsv".format(prefix)) and cache_gwas:
            print("reading gwas results from gwas_out_mapping.tsv...")
            gwas_df=pd.read_csv("{}gwas_out_mapping.tsv".format(prefix),sep="\t")
        else:
            rm_gwas_out="rm {}gwas_out_mapping.tsv".format(prefix)
            subprocess.call(shlex.split(rm_gwas_out),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            gwas_load_df=df.copy()
            gwas_load_df=df_replace_value(gwas_load_df,columns["chrom"],"23","X")
            gwas_df=load_api_summaries(gwas_load_df,gwascatalog_pad,gwascatalog_pval,gwapi,gwascatalog_threads,columns)
            gwas_df=df_replace_value(gwas_df,"chrom","X","23")
            if cache_gwas:
                gwas_df.to_csv("{}gwas_out_mapping.tsv".format(prefix),sep="\t",index=False)
        gwas_rename={"chrom":columns["chrom"],"pos":columns["pos"],"ref":columns["ref"],"alt":columns["alt"],"pval":columns["pval"]}
        gwas_df=gwas_df.rename(columns=gwas_rename)
        if gwas_df.empty:
            summary_df_2=gwas_df
        else:    
            #filter out invalid values
            filter_out_codes=[9, 14, 15, 16, 17, 18]
            gwas_df=gwas_df.loc[~gwas_df.loc[:,"code"].isin(filter_out_codes)]
            gwas_df.loc[:,"#variant"]=create_variant_column(gwas_df,chrom=columns["chrom"],pos=columns["pos"],ref=columns["ref"],alt=columns["alt"])
            summary_df_2=gwas_df
            unique_efos=list(summary_df_2["trait"].unique())
            trait_name_map={}
            for key in unique_efos:
                trait_name_map[key]=gwapi.get_trait(key)
            summary_df_2.loc[:,"trait_name"]=summary_df_2.loc[:,"trait"].apply(lambda x: trait_name_map[x])
            summary_df_2=summary_df_2.drop_duplicates(subset=["#variant","trait"])
    if compare_style not in ["file","gwascatalog","both"]:
        raise NotImplementedError("comparison method '{}' not yet implemented".format(compare_style))
    summary_df=pd.concat([summary_df_1,summary_df_2],sort=True)
    if summary_df.empty:
        #just abort, output the top report but no merging summary df cause it doesn't exist
        print("No summary variants, report will be incomplete")
        report_out_df=df.copy()
        report_out_df["#variant_hit"]="NA"
        report_out_df["pval_trait"]="NA"
        report_out_df["trait"]="NA"
        report_out_df["trait_name"]="NA"
    else:
        summary_df.to_csv("{}summary_df.tsv".format(prefix),sep="\t",index=False)
        summary_df=map_column(summary_df,"map_variant",columns)
        df=map_column(df,"map_variant",columns)
        necessary_columns=[columns["pval"],"#variant","map_variant","trait","trait_name"]
        report_out_df=pd.merge(df,summary_df.loc[:,necessary_columns],how="left",on="map_variant")
        report_out_df=report_out_df.drop(columns=["map_variant"])
        report_out_df=report_out_df.rename(columns={"#variant_x":"#variant","#variant_y":"#variant_hit","{}_x".format(columns["pval"]):columns["pval"],"{}_y".format(columns["pval"]):"pval_trait"})
        report_out_df=report_out_df.sort_values(by=[columns["chrom"],columns["pos"],columns["ref"],columns["alt"],"#variant"])
    #Calculate ld between our variants and external variants
    ld_out=None
    if ld_check and (not summary_df.empty):
        #if no groups in base data
        if (("pos_rmin" not in df.columns.to_list()) or ("pos_rmax" not in df.columns.to_list())):
            Exception("ld calculation not supported without grouping. Please supply the flag --group to main.py or gws_fetch.py.") 
        unique_locus_list=df["locus_id"].unique()
        ld_df=pd.DataFrame()
        df.to_csv("{}df.tsv".format(prefix),index=False,sep="\t")
        #create chromosome list and group loci based on those
        chrom_lst=  sorted([*{*[s.split("_")[0].strip("chr") for s in unique_locus_list]}])
        for chrom in chrom_lst:
            print("------------LD for groups in chromosome {}------------".format(chrom))
            groups=df[df[columns["chrom"]].astype(str) == chrom ].loc[:,"locus_id"].unique()
            plink_cmd="plink --bfile {} --output-chr M --chr {} --make-bed --out {}temp_chrom --memory {}".format( ld_panel_path, chrom , prefix, plink_mem)
            pr=subprocess.run(shlex.split(plink_cmd),stdout=PIPE,stderr=subprocess.STDOUT)
            if pr.returncode!=0:
                print("PLINK FAILURE for chromosome {}. Error code {}".format(chrom,pr.returncode)  )
                print(pr.stdout)
                continue
            for gr in groups:
                ld=extract_ld_variants(df,summary_df,gr,ldstore_threads,ld_treshold,prefix,columns)
                if type(ld)==type(None):
                    continue
                ld_df=pd.concat([ld_df,ld],sort=True)
        c5="rm "
        plink_files=glob.glob("{}temp_chrom.*".format(prefix))
        Popen(shlex.split(c5)+plink_files,stderr=subprocess.DEVNULL)
        if not ld_df.empty:
            ld_df=ld_df.drop_duplicates(subset=["RSID1","RSID2"],keep="first")
            ld_df=ld_df.merge(summary_df.loc[:,["map_variant","trait","trait_name"]].rename(columns={"map_variant":"RSID2_map"}),how="inner",on="RSID2_map")
            ld_out=df.merge(ld_df,how="inner",left_on="#variant",right_on="RSID1")
            ld_out=ld_out.drop(columns=["RSID1","map_variant","RSID2_map"]).rename(columns={"{}_x".format(columns["pval"]):columns["pval"],"{}_y".format(columns["pval"]):"pval_trait","RSID2":"#variant_hit"})
        else:
            print("No variants in ld found, no LD output file produced.")
    return (report_out_df, ld_out)

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Compare found GWS results to previously found results")
    parser.add_argument("compare_fname",type=str,help="GWS result file")
    parser.add_argument("--sign-treshold",dest="sig_treshold",type=float,help="Signifigance treshold",default=5e-8)
    parser.add_argument("--grouping-method",dest="grouping_method",type=str,default="simple",help="Decide grouping method, simple or ld, default simple")
    parser.add_argument("--compare-style",type=str,default="gwascatalog",choices=['file','gwascatalog','both'],help="use 'file', 'gwascatalog' or 'both'")
    parser.add_argument("--custom-dataresource",type=str,default="",help="Custom dataresource path.")
    parser.add_argument("--check-for-ld",dest="ld_check",action="store_true",help="Whether to check for ld between the summary statistics and GWS results")
    parser.add_argument("--plink-memory", dest="plink_mem", type=int, default=12000, help="plink memory for ld clumping, in MB")
    #parser.add_argument("--ld-chromosome-panel-path",dest="ld_chromosome_panel",help="Path to ld panel, where each chromosome is separated. If path is 'path/panel_#chrom.bed', input 'path/panel' ")
    parser.add_argument("--ld-panel-path",dest="ld_panel_path",type=str,help="Filename to the genotype data for ld calculation, without suffix")
    parser.add_argument("--prefix",dest="prefix",type=str,default="",help="output and temporary file prefix. Default value is the base name (no path and no file extensions) of input file. ")
    parser.add_argument("--report-out",dest="report_out",type=str,default="report_out.tsv",help="Report output path")
    parser.add_argument("--ld-report-out",dest="ld_report_out",type=str,default="ld_report_out.tsv",help="LD check report output path")
    parser.add_argument("--gwascatalog-pval",type=float,default=5e-8,help="P-value cutoff for GWASCatalog searches")
    parser.add_argument("--gwascatalog-width-kb",dest="gwascatalog_pad",type=int,default=25,help="gwascatalog range padding")
    parser.add_argument("--gwascatalog-threads",dest="gwascatalog_threads",type=int,default=4,help="Number of concurrent queries to GWAScatalog API. Default 4. Increase if the gwascatalog api takes too long.")
    parser.add_argument("--ldstore-threads",type=int,default=4,help="Number of threads to use with ldstore. Default 4")
    parser.add_argument("--ld-treshold",type=float,default=0.9,help="ld treshold for including ld associations in ld report")
    parser.add_argument("--cache-gwas",action="store_true",help="save gwascatalog results into gwas_out_mapping.tsv and load them from there if it exists. Use only for testing.")
    parser.add_argument("--column-labels",dest="column_labels",metavar=("CHROM","POS","REF","ALT","PVAL","BETA","AF","AF_CASE","AF_CONTROL"),nargs=9,default=["#chrom","pos","ref","alt","pval","beta","maf","maf_cases","maf_controls"],help="Names for data file columns. Default is '#chrom pos ref alt pval beta maf maf_cases maf_controls'.")
    parser.add_argument("--top-report-out",dest="top_report_out",type=str,default="top_report.tsv",help="Top level report filename.")
    parser.add_argument("--strict-group-r2",dest="strict_group_r2",type=float,default=0.5,help="R^2 threshold for including variants in strict groups in top report")
    parser.add_argument("--efo-codes",dest="efo_traits",type=str,nargs="+",default=[],help="Specific EFO codes to look for in the top level report")
    parser.add_argument("--local-gwascatalog",dest='localdb_path',type=str,help="Path to local GWAS Catalog DB.")
    parser.add_argument("--db",dest="database_choice",type=str,choices=['local','gwas','summary_stats'],default="gwas",help="Database to use for comparison. use 'local','gwas' or 'summary_stats'.")
    args=parser.parse_args()
    columns=columns_from_arguments(args.column_labels)
    if args.prefix!="":
        args.prefix=args.prefix+"."
    args.report_out = "{}{}".format(args.prefix,args.report_out)
    args.top_report_out = "{}{}".format(args.prefix,args.top_report_out)
    args.ld_report_out = "{}{}".format(args.prefix,args.ld_report_out)

    gwapi=None
    if args.database_choice=="local":
        gwapi=gwcatalog_api.LocalDB(args.localdb_path)
        args.gwascatalog_threads=1
    elif args.database_choice=="summary_stats":
        gwapi=gwcatalog_api.SummaryApi()
    else:
        gwapi=gwcatalog_api.GwasApi()

    df=pd.read_csv(args.compare_fname,sep="\t")
    customdataresource = None
    if args.custom_dataresource != "":
        customdataresource = custom_catalog.CustomCatalog(args.custom_dataresource)
    [report_df,ld_out_df] = compare(df,compare_style=args.compare_style, ld_check=args.ld_check,
                                    plink_mem=args.plink_mem, ld_panel_path=args.ld_panel_path, prefix=args.prefix,
                                    gwascatalog_pval=args.gwascatalog_pval, gwascatalog_pad=args.gwascatalog_pad, gwascatalog_threads=args.gwascatalog_threads,
                                    ldstore_threads=args.ldstore_threads, ld_treshold=args.ld_treshold, cache_gwas=args.cache_gwas, columns=columns,
                                    gwapi=gwapi, customdataresource_path=args.custom_dataresource)
    if type(report_df) != type(None):
        report_df.to_csv(args.report_out,sep="\t",index=False,float_format="%.3g")
        #top level df
        columns=columns_from_arguments(args.column_labels)
        top_df=create_top_level_report(report_df,efo_traits=args.efo_traits,columns=columns,grouping_method= args.grouping_method,significance_threshold=args.sig_treshold,strict_ld_threshold=args.strict_group_r2)
        top_df.to_csv(args.top_report_out,sep="\t",index=False,float_format="%.3g")
    if type(ld_out_df) != type(None):
        ld_out_df.to_csv(args.ld_report_out,sep="\t",float_format="%.3g")
