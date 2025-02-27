from typing import Optional
import pandas as pd
from scipy.sparse import issparse
from anndata import AnnData
from scanpy import logging
import os
from multiprocessing import cpu_count


def copykat(
    adata: AnnData,
    gene_ids: str = "S",
    segmentation_cut: float = 0.1,
    distance: str = "euclidean",
    s_name: str = "copykat_result",
    min_genes_chr: int = 5,
    key_added: str = "cnv",
    inplace: bool = True,
    layer: str = None,
    n_jobs: Optional[int] = None,
) -> pd.DataFrame:
    """Inference of genomic copy number and subclonal structure.

    Runs CopyKAT (Copynumber Karyotyping of Tumors) :cite:`Gao2021` based on integrative
    Bayesian approaches to identify genome-wide aneuploidy at 5MB resolution
    in single cells to separate tumor cells from normal cells, and tumor
    subclones using high-throughput sc-RNAseq data.

    Note on input data from the original authors:

        The matrix values are often the count of unique molecular identifier (UMI)
        from nowadays high througput single cell RNAseq data. The early generation of
        scRNAseq data may be summarized as TPM values or total read counts,
        which should also work.

    This means that unlike for :func:`infercnvpy.tl.infercnv` the input data
    should not be log-transformed.

    CopyKAT also does NOT require running :func:`infercnvpy.io.genomic_position_from_gtf`,
    it infers the genomic position from the gene symbols in `adata.var_names`.

    You can find more info on GitHub: https://github.com/navinlabcode/copykat

    Parameters
    ----------
    adata
        annotated data matrix
    key_added
        Key under which the copyKAT scores will be stored in `adata.obsm` and `adata.uns`.
    inplace
        If True, store the result in adata, otherwise return it.
    gene_ids
        gene id type: Symbol ("S") or Ensemble ("E").
    segmentation_cut
        segmentation parameters, input 0 to 1; larger looser criteria.
    distance
        distance methods include "euclidean", and correlation coverted distance include "pearson" and "spearman".
    s_name
        sample (output file) name.
    min_genes_chr
        minimal number of genes per chromosome for cell filtering.
    n_jobs
        Number of cores to use for copyKAT analysis. Per default, uses all cores
        available on the system. Multithreading does not work on Windows and this
        value will be ignored.

    Returns
    -------
    Depending on the value of `inplace`, either returns `None` or a vector
    with scores.
    """

    if n_jobs is None:
        n_jobs = cpu_count()
    if os.name != "posix":
        n_jobs = 1

    try:
        from rpy2.robjects.packages import importr
        from rpy2.robjects import pandas2ri, numpy2ri
        from rpy2.robjects.conversion import localconverter
        from rpy2 import robjects as ro
    except ImportError:
        raise ImportError("copyKAT requires rpy2 to be installed. ")

    try:
        copyKAT = importr("copykat")
        tidyverse = importr("stringr")
    except ImportError:
        raise ImportError(
            "copyKAT requires a valid R installation with the following packages: "
            "copykat, stringr"
        )

    logging.info("Preparing R objects")
    with localconverter(ro.default_converter + numpy2ri.converter):
        expr = adata.X if layer is None else tmp_adata.layers[layer]
        if issparse(expr):
            expr = expr.T.toarray()
        else:
            expr = expr.T
        ro.globalenv["expr_r"] = ro.conversion.py2rpy(expr)
    ro.globalenv["gene_names"] = ro.conversion.py2rpy(list(adata.var.index))
    ro.globalenv["cell_IDs"] = ro.conversion.py2rpy(list(adata.obs.index))
    ro.globalenv["n_jobs"] = ro.conversion.py2rpy(n_jobs)
    ro.globalenv["gene_ids"] = ro.conversion.py2rpy(gene_ids)
    ro.globalenv["segmentation_cut"] = ro.conversion.py2rpy(segmentation_cut)
    ro.globalenv["distance"] = ro.conversion.py2rpy(distance)
    ro.globalenv["s_name"] = ro.conversion.py2rpy(s_name)
    ro.globalenv["min_gene_chr"] = ro.conversion.py2rpy(min_genes_chr)

    logging.info("Running copyKAT")
    ro.r(
        f"""
        rownames(expr_r) <- gene_names
        colnames(expr_r) <- cell_IDs
        copyKAT_run <- copykat(rawmat = expr_r, id.type = gene_ids, ngene.chr = min_gene_chr, win.size = 25, 
                                KS.cut = segmentation_cut, sam.name = s_name, distance = distance, norm.cell.names = "", 
                                n.cores = n_jobs, output.seg = FALSE)
        copyKAT_result <- copyKAT_run$CNAmat
        colnames(copyKAT_result) <- str_replace_all(colnames(copyKAT_result), "\\\.", "-")
        """
    )

    with localconverter(
        ro.default_converter + numpy2ri.converter + pandas2ri.converter
    ):
        copyKAT_result = ro.conversion.rpy2py(ro.globalenv["copyKAT_result"])

    chrom_pos = {
        "chr_pos": {
            f"chr{chrom}": int(pos)
            for pos, chrom in copyKAT_result.loc[:, ["chrom"]]
            .drop_duplicates()
            .itertuples()
        }
    }

    # Drop cols
    new_cpkat = copyKAT_result.drop(["chrom", "chrompos", "abspos"], axis=1).values

    # transpose
    new_cpkat_trans = new_cpkat.T

    if inplace:
        adata.uns[key_added] = chrom_pos
        adata.obsm["X_%s" % key_added] = new_cpkat_trans
    else:
        return new_cpkat_trans
