import numpy as np
import json as json_package
import mysql.connector as sql
import time
import logging

# Add and format time stamp in logging messages
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    datefmt="%c",
)
# Mute numexpr threads info
logging.getLogger("numexpr").setLevel(logging.WARNING)

import warnings

warnings.simplefilter(action="ignore", category=UserWarning)
import pandas as pd


# Custom functions
from .utils import gget_species_options, find_latest_ens_rel, wrap_cols_func

from .constants import ENSEMBL_FTP_URL, ENSEMBL_FTP_URL_PLANT

def search(
    searchwords,
    species,
    id_type="gene",
    seqtype=None,
    andor="or",
    limit=None,
    wrap_text=False,
    json=False,
    save=False,
    verbose=True,
):
    """
    Function to query Ensembl for genes based on species and free form search terms.
    Automatically fetches results from latest Ensembl release, unless user specifies database (see 'species' argument).

    Args:
    - searchwords     Free form search words (not case-sensitive) as a string or list of strings
                      (e.g.searchwords = ["GABA", "gamma-aminobutyric"]).
    - species         Species can be passed in the format "genus_species", e.g. "homo_sapiens".
                      To pass a specific database, enter the name of the core database, e.g. 'mus_musculus_dba2j_core_105_1'.
                      All availabale species databases can be found here: http://ftp.ensembl.org/pub/release-106/mysql/
    - id_type         "gene" (default) or "transcript"
                      Defines whether genes or transcripts matching the searchwords are returned.
    - andor           "or" (default) or "and"
                      "or": Returns all genes that INCLUDE AT LEAST ONE of the searchwords in their name/description.
                      "and": Returns only genes that INCLUDE ALL of the searchwords in their name/description.
    - limit           (int) Limit the number of search results returned (default: None).
    - wrap_text       If True, displays data frame with wrapped text for easy reading. Default: False.
    - json            If True, returns results in json format instead of data frame. Default: False.
    - save            If True, the data frame is saved as a csv in the current directory (default: False).
    - verbose         True/False whether to print progress information. Default True.

    Returns a data frame with the query results.

    Deprecated arguments: 'seqtype' (renamed to id_type)
    """
    # Handle deprecated arguments
    if seqtype:
        logging.error(
            "'seqtype' argument deprecated! Please use argument 'id_type' instead."
        )
        return

    start_time = time.time()

    # Find latest Ensembl release
    ens_rel = find_latest_ens_rel()

    ## Check validity or arguments
    # Check if id_type is valid
    id_types = ["gene", "transcript"]
    id_type = id_type.lower()
    if id_type not in id_types:
        raise ValueError(
            f"ID type (id_type) specified is '{id_type}'. Expected one of: {', '.join(id_types)}"
        )

    # Check if 'andor' arg is valid
    andors = ["and", "or"]
    andor = andor.lower()
    if andor not in andors:
        raise ValueError(
            f"'andor' argument specified as {andor}. Expected one of {', '.join(andors)}"
        )

    ## Get database for specified species
    # Species shortcuts
    if species == "human":
        species = "homo_sapiens"
    if species == "mouse":
        species = "mus_musculus"

    # If a specific database is passed with the "/" at the end, remove it
    if "/" in species:
        species = species.split("/")[0]

    # In case species was passed with upper case letters
    species = species.lower()

    # Fetch ensembl databases
    databases = gget_species_options(
        database=ENSEMBL_FTP_URL, release=None
    )

    # Add ensembl plant databases
    databases += gget_species_options(
        database=ENSEMBL_FTP_URL_PLANT, release=None
    )

    db = []
    for datab in databases:
        if species in datab:
            db.append(datab)

    # Unless an unambigious mouse database is specified,
    # the standard core database will be used
    if len(db) > 1 and "mus_musculus" in species:
        db = f"mus_musculus_core_{ens_rel}_39"

    # Check for ambigious species matches in species other than mouse
    elif len(db) > 1 and "mus_musculus" not in species:
        raise ValueError(
            "Species matches more than one database.\n"
            "Please double-check spelling or pass specific CORE database.\n"
            "All available databases can be found here:\n"
            f"http://ftp.ensembl.org/pub/release-{ens_rel}/mysql/"
        )
    # Raise error if no matching database was found
    elif len(db) == 0:
        raise ValueError(
            "Species not found in database.\n"
            "Please double-check spelling or pass specific CORE database.\n"
            "All available databases can be found here:\n"
            f"http://ftp.ensembl.org/pub/release-{ens_rel}/mysql/"
        )

    else:
        db = db[0]

    if verbose:
        logging.info(f"Fetching results from database: {db}")

    ## Connect to data base
    try:
        db_connection = sql.connect(
            host="mysql-eg-publicsql.ebi.ac.uk", database=db, user="anonymous", password=""
        )
    except:
        try:
            # Try different port
            db_connection = sql.connect(
                host="mysql-eg-publicsql.ebi.ac.uk",
                database=db,
                user="anonymous",
                password="",
                port=4157,
            )

        except Exception as e:
            logging.error(f"The Ensembl server returned the following error: {e}")

    ## Clean up list of searchwords
    # If single searchword passed as string, convert to list
    if type(searchwords) == str:
        searchwords = [searchwords]

    ## Find genes
    for i, searchword in enumerate(searchwords):
        if id_type == "gene":
            query = f"""
            SELECT gene.stable_id AS 'ensembl_id', xref.display_label AS 'gene_name', gene.description AS 'ensembl_description', xref.description AS 'ext_ref_description', gene.biotype AS 'biotype'
            FROM gene
            LEFT JOIN xref ON gene.display_xref_id = xref.xref_id
            WHERE (gene.description LIKE '%{searchword}%' OR xref.description LIKE '%{searchword}%' OR xref.display_label LIKE '%{searchword}%')
            """

            # Fetch the search results from the host using the specified query
            df_temp = pd.read_sql(query, con=db_connection)

            # Order by ENSEMBL ID (I am using pandas for this instead of SQL to increase speed)
            df_temp = df_temp.sort_values("ensembl_id").reset_index(drop=True)

            # If andor="or", keep all results
            if andor == "or":
                # In the first iteration, make the search results equal to the master data frame
                if i == 0:
                    df = df_temp.copy()
                # Add new search results to master data frame
                else:
                    df = pd.concat([df, df_temp])

            # If andor="and", only keep overlap between results
            if andor == "and":
                # In the first iteration, make the search results equal to the master data frame
                if i == 0:
                    df = df_temp.copy()
                # Only keep overlapping results in master data frame
                else:
                    val = np.intersect1d(df["ensembl_id"], df_temp["ensembl_id"])
                    df = df[df.ensembl_id.isin(val)]

        if id_type == "transcript":
            query = f"""
            SELECT transcript.stable_id AS 'ensembl_id', xref.display_label AS 'gene_name', transcript.description AS 'ensembl_description', xref.description AS 'ext_ref_description', transcript.biotype AS 'biotype'
            FROM transcript
            LEFT JOIN xref ON transcript.display_xref_id = xref.xref_id
            WHERE (transcript.description LIKE '%{searchword}%' OR xref.description LIKE '%{searchword}%' OR xref.display_label LIKE '%{searchword}%')
            """

            # Fetch the search results from the host using the specified query
            df_temp = pd.read_sql(query, con=db_connection)
            # Order by ENSEMBL ID (I am using pandas for this instead of SQL to increase speed)
            df_temp = df_temp.sort_values("ensembl_id").reset_index(drop=True)

            # If andor="or", keep all results
            if andor == "or":
                # In the first iteration, make the search results equal to the master data frame
                if i == 0:
                    df = df_temp.copy()
                # Add new search results to master data frame
                else:
                    df = pd.concat([df, df_temp])

            # If andor="and", only keep overlap between results
            if andor == "and":
                # In the first iteration, make the search results equal to the master data frame
                if i == 0:
                    df = df_temp.copy()
                # Only keep overlapping results in master data frame
                else:
                    val = np.intersect1d(df["ensembl_id"], df_temp["ensembl_id"])
                    df = df[df.ensembl_id.isin(val)]

    # Remove any duplicate search results from the master data frame and reset the index
    df = df.drop_duplicates().reset_index(drop=True)

    # If limit is not None, keep only the first {limit} rows
    if limit != None:
        # Print number of genes/transcripts found versus fetched
        if verbose:
            logging.info(f"Returning {limit} matches of {len(df)} total matches found.")
        # Remove all but limit rows
        df = df.head(limit)

    else:
        # Print number of genes/transcripts fetched
        if verbose:
            logging.info(f"Total matches found: {len(df)}.")

    # Print query time
    if verbose:
        logging.info(f"Query time: {round(time.time() - start_time, 2)} seconds.")

    # Add URL to gene summary on Ensembl
    df["url"] = (
        "https://useast.ensembl.org/"
        + "_".join(db.split("_")[:2])
        + "/Gene/Summary?g="
        + df["ensembl_id"]
    )

    if wrap_text:
        df_wrapped = df.copy()
        wrap_cols_func(
            df_wrapped, ["ensembl_description", "ext_ref_description", "url"]
        )

    if json:
        results_dict = json_package.loads(df.to_json(orient="records"))
        if save:
            with open("gget_search_results.json", "w", encoding="utf-8") as f:
                json_package.dump(results_dict, f, ensure_ascii=False, indent=4)

        # Return results in json format
        return results_dict

    else:
        # Save
        if save:
            df.to_csv("gget_search_results.csv", index=False)

        # Return data frame
        return df
