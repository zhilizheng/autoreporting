#!/usr/bin/env python3

import argparse,shlex,subprocess,os
from subprocess import Popen, PIPE
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from autoreporting_utils import *
#TODO: make a system for making sure we can calculate all necessary fields,
#e.g by checking that the columns exist


def calculate_enrichment(gnomad_df,fi_af_col,count_nfe_lst,number_nfe_lst):
    """Calculate enrichment for finns vs the other group, 
    which is defined in the column name lists
    In: gnomad dataframe, finnish allele frequency column,
    other group allele count and number columns
    Out: enrichment column"""
    nfe_counts=gnomad_df.loc[:,count_nfe_lst].sum(axis=1,skipna=True)
    nfe_numbers=gnomad_df.loc[:,number_nfe_lst].sum(axis=1,skipna=True)
    finn_freq=gnomad_df.loc[:,fi_af_col]
    enrichment=nfe_numbers*finn_freq/nfe_counts
    # clip enrichment
    enrichment=enrichment.clip(lower=0.0,upper=1e6)
    return enrichment

def create_rename_dict(list_of_names: List[str], prefix: str) -> Dict[str,str]:
    """
    Create a dictionary for renaming columns from different annotation files
    For example, given column names ["AF_1","AF_2","AF_3"] and a prefix "GNOMAD_",
    return rename_dict={"AF_1":"GNOMAD_AF1","AF_2":"GNOMAD_AF2","AF_3":"GNOMAD_AF3"}
    This can then be used as df.rename(columns=rename_dict)
    Args:
        list_of_names (List[str]): list of names to prefix
        prefix (str): prefix string
    Returns:
        (pd.DataFrame): Dict with items name:prefixname
    """
    d={}
    for value in list_of_names:
        d[value]="{}{}".format(prefix,value)
    return d

def previous_release_annotate(fpath: Optional[str], df: pd.DataFrame, columns: Dict[str,str]) -> pd.DataFrame:
    """Create the previous release annotation
    Args:
        fpath (str): filepath to the previous release summary statistic
        df (pd.DataFrame): Variant dataframe
        columns (Dict[str,str]): column dictionary
    Returns:
        (pd.DataFrame): Dataframe with columns [#variant, beta_previous_release, pval_previous_release]
    """
    out_columns = ["#variant",
        "beta_previous_release",
        "pval_previous_release"]
    previous_cols = [columns["chrom"],
        columns["pos"],
        columns["ref"],
        columns["alt"],
        "beta_previous_release",
        "pval_previous_release"]

    if fpath:
        if not os.path.exists("{}.tbi".format(fpath)):
            raise FileNotFoundError("Tabix index for file {} not found. Make sure that the file is properly indexed.".format(fpath))
        previous_df = load_annotation_df(df,fpath,columns,columns, chrom_prefix="", na_value="")
    else:
        return pd.DataFrame(columns = out_columns)

    previous_df = previous_df.rename(columns={"beta":"beta_previous_release","pval":"pval_previous_release"})
    previous_df = previous_df[previous_cols]    

    if not previous_df.empty:
        previous_df = previous_df.drop_duplicates(subset=[columns["chrom"], columns["pos"], columns["ref"], columns["alt"]])
        previous_df = df_replace_value(previous_df,columns["chrom"],"X","23")
        previous_df["#variant"] = create_variant_column(previous_df,chrom=columns["chrom"],pos=columns["pos"],ref=columns["ref"],alt=columns["alt"])
    else:
        previous_df["#variant"] = np.nan
    
    previous_df = previous_df[out_columns]
    return previous_df

def functional_annotate(df: pd.DataFrame, functional_path: Optional[str], columns: Dict[str,str]) -> pd.DataFrame:
    """Annotate variants with functional consequence
    Args:
        df (pd.DataFrame): Input dataframe
        functional_path (str): Annotation file path
        columns (Dict[str,str]): column names
    Returns:
        (pd.DataFrame): Dataframe with columns for variant id, 
        fin.AF,fin_AN,fin_AC, fin.homozygote_count, fet_nfsee.odds_ratio , 
        fet_nfsee.p_value, nfsee.AC, nfsee.AN, nfsee.AF, nfsee.homozygote_count
    """
    return_columns = ["#variant", 
        "enrichment_nfsee",
        "fin.AF",
        "fin.AN",
        "fin.AC",
        "fin.homozygote_count",
        "fet_nfsee.odds_ratio",
        "fet_nfsee.p_value",
        "nfsee.AC",
        "nfsee.AN",
        "nfsee.AF",
        "nfsee.homozygote_count"]

    resource_cols = {
        "chrom":"chrom",
        "pos":"pos",
        "ref":"ref",
        "alt":"alt"
    }

    col_rename_dict = {
        "chrom":columns["chrom"],
        "pos":columns["pos"],
        "ref":columns["ref"],
        "alt":columns["alt"],
    }

    if (not functional_path) or df.empty:
        return pd.DataFrame(columns=return_columns)
    if not os.path.exists(functional_path):
        raise FileNotFoundError("File {} not found. Make sure that the file exists.".format(functional_path))
    if not os.path.exists("{}.tbi".format(functional_path)): #should really be handled by the tabix loader
        raise FileNotFoundError("Tabix index for file {} not found. Make sure that the file is properly indexed.".format(functional_path))
    
    func_df = load_annotation_df(df,functional_path,columns,resource_cols,chrom_prefix="chr",na_value="NA")
    func_df["chrom"] = func_df["chrom"].apply(lambda x:x.strip("chr"))
    func_df=df_replace_value(func_df,"chrom","X","23")
    func_df = func_df.drop_duplicates(subset=["chrom","pos","ref","alt"]).rename(columns=col_rename_dict)
    

    func_df["#variant"] = create_variant_column(func_df,chrom=columns["chrom"],pos=columns["pos"],ref=columns["ref"],alt=columns["alt"])

    return func_df[return_columns]

def finngen_annotate(df: pd.DataFrame, finngen_path: Optional[str], columns: Dict[str,str]) -> pd.DataFrame:
    """Annotate variants with finngen annotations
    Args:
        df (pd.DataFrame): Input dataframe
        finngen_path (str): Annotation file path
        columns (Dict[str,str]): column names
    Returns:
        (pd.DataFrame): Dataframe with columns #variant,
            most_severe_gene,
            most_severe_consequence,
            FG_INFO,
            n_INFO_gt_0_6,
            functional_category
    """
    finngen_cols=[
        "most_severe_gene",
        "most_severe_consequence",
        "FG_INFO",
        "n_INFO_gt_0_6",
        "functional_category"
    ]
    resource_cols = {
        "chrom":"chr",
        "pos":"pos",
        "ref":"ref",
        "alt":"alt"
    }
    functional_categories=[
        "transcript_ablation",
        "splice_donor_variant",
        "stop_gained",
        "splice_acceptor_variant",
        "frameshift_variant",
        "stop_lost",
        "start_lost",
        "inframe_insertion",
        "inframe_deletion",
        "missense_variant",
        "protein_altering_variant"
    ]
    col_rename_d = {
        "gene_most_severe":"most_severe_gene",
        "most_severe":"most_severe_consequence",
        "INFO":"FG_INFO"
    }
    if (not finngen_path) or df.empty:
        return pd.DataFrame(columns=["#variant"])
    if not os.path.exists(finngen_path):
        raise FileNotFoundError("File {} not found. Make sure that the file exists.".format(finngen_path))
    if not os.path.exists("{}.tbi".format(finngen_path)):
        raise FileNotFoundError("Tabix index for file {} not found. Make sure that the file is properly indexed.".format(finngen_path))

    fg_df=load_annotation_df(df,finngen_path,columns,resource_cols,chrom_prefix="",na_value="NA")

    fg_df=fg_df.drop(labels="#variant",axis="columns")
    fg_df["#variant"]=create_variant_column(fg_df,chrom="chr",pos="pos",ref="ref",alt="alt")
    fg_df = fg_df.drop_duplicates(subset=["#variant"])
    #file version check: if number of variants is >0 and FG annotations are smaller, emit a warning message.
    if df.shape[0]>0 and fg_df.shape[0]==0:
        print("Warning: FG annotation does not have any hits but the input data has. Check that you are using a recent version of the finngen annotation file (R3_v1 or above)")
    
    fg_df=fg_df.rename(columns=col_rename_d)

    #create functional category col. np.where syntax: np.where(condition,value_if_true,value_if_false)
    fg_df["functional_category"] = np.where(
        fg_df["most_severe_consequence"].isin(functional_categories),
        fg_df["most_severe_consequence"],
        np.nan
    )
    #calculate how many batchwise info vals > 0.6
    fg_cols=fg_df.columns.values.tolist()
    infocols = list(filter(lambda s: "INFO_" in s,fg_cols))
    fg_df["n_INFO_gt_0_6"] = np.sum(fg_df[infocols]>0.6,axis=1)
    
    #subset final columns
    fg_df=fg_df.loc[:,["#variant"]+finngen_cols]
    
    return fg_df

def gnomad_gen_annotate(df: pd.DataFrame, gnomad_path: Optional[str], columns: Dict[str, str]) -> pd.DataFrame:
    """Annotate variants with gnomad genome annotations
    Args:
        df (pd.DataFrame): input dataframe, with chromosome containing X instead of 23
        gnomad_path (Optional[str]): gnomad filepath
        columns (Dict[str, str]): input dataframe column dictionary
    Returns:
        (pd.DataFrame): dataframe with columns for variant id, allele frequencies, enrichment
    """
    #gnomad out columns
    gnomad_gen_cols=["AF_fin",
        "AF_nfe",
        "AF_nfe_est",
        "AF_nfe_nwe",
        "AF_nfe_onf",
        "AF_nfe_seu",
        "FI_enrichment_nfe",
        "FI_enrichment_nfe_est"]

    resource_cols = {
        "chrom":"#CHROM",
        "pos":"POS",
        "ref":"REF",
        "alt":"ALT"
    }
    gn_gen_rename_d=create_rename_dict(gnomad_gen_cols,"GENOME_")
    out_columns = list(gn_gen_rename_d.values())

    if (not gnomad_path) or df.empty:
        return pd.DataFrame(columns=["#variant"]+out_columns)
    if not os.path.exists(gnomad_path):
        raise FileNotFoundError("File {} not found. Make sure that the file exists.".format(gnomad_path))
    if not os.path.exists("{}.tbi".format(gnomad_path)):
        raise FileNotFoundError("Tabix index for file {} not found. Make sure that the file is properly indexed.".format(gnomad_path))

    gnomad_genomes=load_annotation_df(df,gnomad_path,columns,resource_cols)
    gnomad_genomes = df_replace_value(gnomad_genomes,"#CHROM","X","23")
    gnomad_genomes=gnomad_genomes.drop_duplicates(subset=["#CHROM","POS","REF","ALT"]).rename(columns={"#CHROM":columns["chrom"],"POS":columns["pos"],"REF":columns["ref"],"ALT":columns["alt"]})
    gnomad_genomes["#variant"]=create_variant_column(gnomad_genomes,chrom=columns["chrom"],pos=columns["pos"],ref=columns["ref"],alt=columns["alt"])
    #calculate enrichment for gnomad genomes, nfe, nfe without est
    gn_gen_nfe_counts=["AC_nfe_est","AC_nfe_nwe","AC_nfe_onf","AC_nfe_seu"]
    gn_gen_nfe_nums=["AN_nfe_est","AN_nfe_nwe","AN_nfe_onf","AN_nfe_seu"]
    gn_gen_nfe_est_counts=["AC_nfe_nwe","AC_nfe_onf","AC_nfe_seu"]
    gn_gen_nfe_est_nums=["AN_nfe_nwe","AN_nfe_onf","AN_nfe_seu"]

    gnomad_genomes.loc[:,"FI_enrichment_nfe"]=calculate_enrichment(gnomad_genomes,"AF_fin",gn_gen_nfe_counts,gn_gen_nfe_nums)
    gnomad_genomes.loc[:,"FI_enrichment_nfe_est"]=calculate_enrichment(gnomad_genomes,"AF_fin",gn_gen_nfe_est_counts,gn_gen_nfe_est_nums)

    gnomad_genomes=gnomad_genomes.loc[:,["#variant"]+gnomad_gen_cols]
    gnomad_genomes=gnomad_genomes.rename(columns=gn_gen_rename_d)
    return gnomad_genomes

def gnomad_exo_annotate(df: pd.DataFrame, gnomad_path: str, columns: Dict[str, str]) -> pd.DataFrame:
    """Annotate variants with gnomad exome annotations
    Args:
        df (pd.DataFrame): input dataframe, with chromosome containing X instead of 23
        gnomad_path (Optional[str]): gnomad filepath
        columns (Dict[str, str]): input dataframe column dictionary
    Returns:
        (pd.DataFrame): dataframe with columns for variant id, allele frequencies, enrichment
    """
    #gnomad out columns
    gnomad_exo_cols=["AF_nfe_bgr",
        "AF_fin",
        "AF_nfe",
        "AF_nfe_est",
        "AF_nfe_swe",
        "AF_nfe_nwe",
        "AF_nfe_onf",
        "AF_nfe_seu",
        "FI_enrichment_nfe",
        "FI_enrichment_nfe_est",
        "FI_enrichment_nfe_swe",
        "FI_enrichment_nfe_est_swe"]

    resource_cols = {
        "chrom":"#CHROM",
        "pos":"POS",
        "ref":"REF",
        "alt":"ALT"
    }
    gn_exo_rename_d=create_rename_dict(gnomad_exo_cols,"EXOME_")
    out_columns = list(gn_exo_rename_d.values())

    if (not gnomad_path) or df.empty:
        return pd.DataFrame(columns=["#variant"]+out_columns)
    if not os.path.exists(gnomad_path):
        raise FileNotFoundError("File {} not found. Make sure that the file exists.".format(gnomad_path))
    if not os.path.exists("{}.tbi".format(gnomad_path)):
        raise FileNotFoundError("Tabix index for file {} not found. Make sure that the file is properly indexed.".format(gnomad_path))
    
    gnomad_exomes=load_annotation_df(df,gnomad_path,columns,resource_cols)
    gnomad_exomes = df_replace_value(gnomad_exomes,"#CHROM","X","23")
    gnomad_exomes=gnomad_exomes.drop_duplicates(subset=["#CHROM","POS","REF","ALT"]).rename(columns={"#CHROM":columns["chrom"],"POS":columns["pos"],"REF":columns["ref"],"ALT":columns["alt"]})
    gnomad_exomes["#variant"]=create_variant_column(gnomad_exomes,chrom=columns["chrom"],pos=columns["pos"],ref=columns["ref"],alt=columns["alt"])
    #calculate enrichment for gnomax exomes, nfe, nfe without est, nfe without swe, nfe without est, swe?
    gn_exo_nfe_counts=["AC_nfe_bgr","AC_nfe_est","AC_nfe_onf","AC_nfe_seu","AC_nfe_swe"]
    gn_exo_nfe_nums=["AN_nfe_bgr","AN_nfe_est","AN_nfe_onf","AN_nfe_seu","AN_nfe_swe"]
    gn_exo_nfe_est_counts=["AC_nfe_bgr","AC_nfe_onf","AC_nfe_seu","AC_nfe_swe"]
    gn_exo_nfe_est_nums=["AN_nfe_bgr","AN_nfe_onf","AN_nfe_seu","AN_nfe_swe"]
    gn_exo_nfe_swe_counts=["AC_nfe_bgr","AC_nfe_est","AC_nfe_onf","AC_nfe_seu"]
    gn_exo_nfe_swe_nums=["AN_nfe_bgr","AN_nfe_est","AN_nfe_onf","AN_nfe_seu"]
    gn_exo_nfe_est_swe_counts=["AC_nfe_bgr","AC_nfe_onf","AC_nfe_seu"]
    gn_exo_nfe_est_swe_nums=["AN_nfe_bgr","AN_nfe_onf","AN_nfe_seu"]

    gnomad_exomes.loc[:,"FI_enrichment_nfe"]=calculate_enrichment(gnomad_exomes,"AF_fin",gn_exo_nfe_counts,gn_exo_nfe_nums)
    gnomad_exomes.loc[:,"FI_enrichment_nfe_est"]=calculate_enrichment(gnomad_exomes,"AF_fin",gn_exo_nfe_est_counts,gn_exo_nfe_est_nums)
    gnomad_exomes.loc[:,"FI_enrichment_nfe_swe"]=calculate_enrichment(gnomad_exomes,"AF_fin",gn_exo_nfe_swe_counts,gn_exo_nfe_swe_nums)
    gnomad_exomes.loc[:,"FI_enrichment_nfe_est_swe"]=calculate_enrichment(gnomad_exomes,"AF_fin",gn_exo_nfe_est_swe_counts,gn_exo_nfe_est_swe_nums)

    gnomad_exomes=gnomad_exomes.loc[:,["#variant"]+gnomad_exo_cols]
    gnomad_exomes=gnomad_exomes.rename(columns=gn_exo_rename_d)
    return gnomad_exomes

def annotate(df: pd.DataFrame, gnomad_genome_path: str, gnomad_exome_path: str, finngen_path: str, functional_path: str, previous_release_path: str ,prefix: str, columns: Dict[str, str]) -> pd.DataFrame :
    """
    Annotates variants with allele frequencies, enrichment numbers, and most severe gene/consequence data
    Annotations from gnomad exome data, gnomad genome data, finngen annotation file, functional annotation file.
    Args:
        df (pd.DataFrame): Variant dataframe
        gnomad_genome_path (str): gnomad genome annotation file path
        gnomad_exome_path (str): gnomad exome annotation file path
        finngen_path (str): finngen annotation file path
        functional_path (str): functional annotation file path
        previous_release_path (str): filepath for the previous release
        prefix (str): prefix for analysis files
        columns (Dict[str, str]): column dictionary
    Returns:
        (pd.DataFrame): Annotated dataframe
    Out: Annotated dataframe
    """

    if df.empty:
        return df

    #create chr 23->X calling df
    #needs chromosome 23 as X
    call_df_x = df.copy()
    call_df_x[columns["chrom"]]=call_df_x[columns["chrom"]].astype(str)
    call_df_x = df_replace_value(call_df_x,columns["chrom"],"23","X") #TODO: IF/WHEN GNOMAD RESOURCES USE CHR 23, THIS NEEDS TO BE REMOVED
    
    #load annotation dataframes
    previous_df = previous_release_annotate(previous_release_path,call_df_x,columns)
    func_df = functional_annotate(call_df_x, functional_path, columns)
    gnomad_genomes = gnomad_gen_annotate(call_df_x,gnomad_genome_path,columns)
    gnomad_exomes = gnomad_exo_annotate(call_df_x,gnomad_exome_path,columns)
    fg_df = finngen_annotate(df,finngen_path,columns)

    #merge the wanted columns into df
    df=df.merge(gnomad_genomes,how="left",on="#variant")
    df=df.merge(gnomad_exomes,how="left",on="#variant")
    df=df.merge(func_df,how="left",on="#variant")
    df=df.merge(fg_df,how="left",on="#variant")
    df=df.merge(previous_df,how="left",on="#variant")

    return df

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Annotate results using gnoMAD and additional annotations")
    parser.add_argument("annotate_fpath",type=str,help="Filepath of the results to be annotated")
    parser.add_argument("--gnomad-genome-path",dest="gnomad_genome_path",type=str,help="Gnomad genome annotation file filepath")
    parser.add_argument("--gnomad-exome-path",dest="gnomad_exome_path",type=str,help="Gnomad exome annotation file filepath")
    parser.add_argument("--finngen-path",dest="finngen_path",type=str,help="Finngen annotation file filepath")
    parser.add_argument("--functional-path",dest="functional_path",type=str,help="File path to functional annotations file")
    parser.add_argument("--previous-release-path",dest="previous_release_path",type=str,help="File path to previous release summary statistic file")
    parser.add_argument("--prefix",dest="prefix",type=str,default="",help="output and temporary file prefix. Default value is the base name (no path and no file extensions) of input file. ")
    parser.add_argument("--annotate-out",dest="annotate_out",type=str,default="annotate_out.tsv",help="Output filename, default is out.tsv")
    parser.add_argument("--column-labels",dest="column_labels",metavar=("CHROM","POS","REF","ALT","PVAL","BETA","AF","AF_CASE","AF_CONTROL"),nargs=9,default=["#chrom","pos","ref","alt","pval","beta","maf","maf_cases","maf_controls"],help="Names for data file columns. Default is '#chrom pos ref alt pval beta maf maf_cases maf_controls'.")
    args=parser.parse_args()
    columns=columns_from_arguments(args.column_labels)
    if args.prefix!="":
        args.prefix=args.prefix+"."
    args.annotate_out = "{}{}".format(args.prefix,args.annotate_out)
    if (args.gnomad_exome_path == None) or (args.gnomad_genome_path == None) or (args.finngen_path==None):
        print("Annotation files missing, aborting...")
    else:    
        input_df = pd.read_csv(args.annotate_fpath,sep="\t")
        df = annotate(df=input_df,gnomad_genome_path=args.gnomad_genome_path, gnomad_exome_path=args.gnomad_exome_path, finngen_path=args.finngen_path,
        functional_path=args.functional_path, previous_release_path=args.previous_release_path, prefix=args.prefix, columns=columns)
        df.fillna("NA").replace("","NA").to_csv(path_or_buf=args.annotate_out,sep="\t",index=False,float_format="%.3g")
