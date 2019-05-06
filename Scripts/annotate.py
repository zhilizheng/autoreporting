#! /usr/bin/python3

import argparse,shlex,subprocess
import pandas as pd 
import numpy as np

def tabix_command(df,path,prefix=""):
    tcall="tabix "+path+" -h " 
    for t in df.itertuples():
        tcall=tcall+" "+str(prefix)+str(t._1)+":"+str(t.pos)+"-"+str(t.pos)+" "
    return tcall

def create_variant_column(df,chrom="#chrom",pos="pos",ref="ref",alt="alt"):
    return "chr{}_{}_{}_{}".format(df.loc[:,chrom].map(str), df.loc[:,pos].map(str), df.loc[:,ref].map(str),df.loc[:,alt].map(str))

def annotate(args):
    #load main file
    df=pd.read_csv(args.annotate_fpath,sep="\t")
    original_cols=df.columns.values.tolist()
    #create tabix command
    tcall=tabix_command(df,args.gnomad_path)
    call=shlex.split(tcall)
    with open("temp_annotate_tbx.out","w") as out: 
        tbx=subprocess.run(call,stdout=out)
    annotation_tbx=pd.read_csv("temp_annotate_tbx.out",sep="\t")
    annotation_tbx=annotation_tbx.drop_duplicates(subset=["#CHROM","POS","REF","ALT"]).rename(columns={"#CHROM":"#chrom","POS":"pos","REF":"ref","ALT":"alt"})
    #add gnomad annotations, by fetching the correct rows using tabix and calculating the necessary features
    allele_frequency_groups=["AF_fin","AF_nfe","AF_nfe_est","AF_nfe_nwe","AF_nfe_onf","AF_nfe_seu"]
    #join based on chrom, pos, ref, and alt
    df=df.merge(annotation_tbx,on=["#chrom","pos","ref","alt"],how="left")
    all_counts_nfe=["AC_nfe","AC_nfe_est","AC_nfe_nwe","AC_nfe_onf","AC_nfe_seu"]
    all_number_nfe=["AN_nfe","AN_nfe_est","AN_nfe_nwe","AN_nfe_onf","AN_nfe_seu"]
    df.loc[:,"AC_sum_nfe"]=df[all_counts_nfe].sum(axis=1)
    df.loc[:,"AN_sum_nfe"]=df[all_number_nfe].sum(axis=1)
    df.loc[:,"FI_enrichment_nfe"]=df.loc[:,"AN_sum_nfe"]*df.loc[:,"AF_fin"]/df.loc[:,"AC_sum_nfe"]
    df.loc[:,"FI_enrichment_nfe"]=df.loc[:,"FI_enrichment_nfe"].clip(lower=0.0,upper=1e6)#replace inf with 1 000 000
    #same for non-estonian europeans 
    all_counts_nfe=["AC_nfe","AC_nfe_nwe","AC_nfe_onf","AC_nfe_seu"]
    all_number_nfe=["AN_nfe","AN_nfe_nwe","AN_nfe_onf","AN_nfe_seu"]
    df.loc[:,"AC_sum_nfe_est"]=df[all_counts_nfe].sum(axis=1)
    df.loc[:,"AN_sum_nfe_est"]=df[all_number_nfe].sum(axis=1)
    df.loc[:,"FI_enrichment_nfe_no_est"]=df.loc[:,"AN_sum_nfe_est"]*df.loc[:,"AF_fin"]/df.loc[:,"AC_sum_nfe_est"]
    df.loc[:,"FI_enrichment_nfe_no_est"]=df.loc[:,"FI_enrichment_nfe_no_est"].clip(0.0,1e6)#replace inf with 1 000 000
    
    #add finngen annotations, using tabix
    tcall=tabix_command(df,args.finngen_path,prefix="chr")
    call=shlex.split(tcall)
    with open("temp_fg_tbx.out","w") as out: 
        tbx=subprocess.run(call,stdout=out)
    fg_df=pd.read_csv("temp_fg_tbx.out",sep="\t")
    fg_df=fg_df.drop_duplicates(subset=["#variant"])
    #add #variant column in df
    df.loc[:,"#variant"]=create_variant_column(df)
    #join based on variant
    fg_cols=fg_df.columns.values.tolist()
    colist=list(set(fg_cols)-set(["chrom","pos","ref","alt"]))
    fg_df=fg_df.loc[:,]
    df=df.merge(fg_df.loc[:,colist],on="#variant",how="left")
    fg_out=["most_severe","INFO","gene"]
    
    #get batch columns, for INFO, IMP, AF
    info=list(filter(lambda s: "INFO_" in s,fg_cols))
    imp=list(filter(lambda s: "IMP_" in s,fg_cols))
    af=list(filter(lambda s: "AF_" in s,fg_cols))
    fg_batches=af+info+imp
    
    out_columns=original_cols+allele_frequency_groups+["FI_enrichment_nfe","FI_enrichment_nfe_no_est"]+fg_out
    if args.batch_freq:
        out_columns+=fg_batches

    df.loc[:,out_columns].to_csv(path_or_buf=args.out_fname,sep="\t",index=False)
    #delete temporary tabix files
    subprocess.run(shlex.split("rm temp_annotate_tbx.out"))
    subprocess.run(shlex.split("rm temp_fg_tbx.out"))

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Annotate results using gnoMAD and additional annotations")
    parser.add_argument("annotate_fpath",type=str,help="Filepath of the results to be annotated")
    parser.add_argument("-g","--gnomad-path",dest="gnomad_path",type=str,help="Gnomad annotation file filepath")
    parser.add_argument("--include-batch-freq",dest="batch_freq",action="store_true",help="Include batch frequencies from finngen annotations")
    parser.add_argument("--finngen-path",dest="finngen_path",type=str,default=None,help="Finngen annotation file filepath")
    parser.add_argument("-o","--out-fname",dest="out_fname",type=str,default="out.csv",help="Output filename, default is out.csv")
    args=parser.parse_args()
    annotate(args)