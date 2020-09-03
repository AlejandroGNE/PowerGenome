import collections
from copy import deepcopy
import subprocess

import pandas as pd
import pudl
import requests
import sqlalchemy as sa

import yaml
from ruamel.yaml import YAML
from pathlib import Path

from powergenome.params import SETTINGS


def load_settings(path):

    with open(path, "r") as f:
        #     settings = yaml.safe_load(f)
        yaml = YAML(typ="safe")
        settings = yaml.load(f)

    return settings


def init_pudl_connection(freq="YS"):

    pudl_engine = sa.create_engine(
        SETTINGS["pudl_db"]
    )  # pudl.init.connect_db(SETTINGS)
    pudl_out = pudl.output.pudltabl.PudlTabl(freq=freq, pudl_engine=pudl_engine)

    return pudl_engine, pudl_out


def reverse_dict_of_lists(d):
    if isinstance(d, collections.abc.Mapping):
        rev = {v: k for k in d for v in d[k]}
    else:
        rev = dict()
    return rev


def map_agg_region_names(df, region_agg_map, original_col_name, new_col_name):

    df[new_col_name] = df.loc[:, original_col_name]

    df.loc[df[original_col_name].isin(region_agg_map.keys()), new_col_name] = df.loc[
        df[original_col_name].isin(region_agg_map.keys()), original_col_name
    ].map(region_agg_map)

    return df


def snake_case_col(col):
    "Remove special characters and convert to snake case"
    clean = (
        col.str.lower()
        .str.replace("[^0-9a-zA-Z\-]+", " ")
        .str.replace("-", "")
        .str.strip()
        .str.replace(" ", "_")
    )
    return clean


def snake_case_str(s):
    "Remove special characters and convert to snake case"
    clean = (
        s.lower()
        .replace("[^0-9a-zA-Z\-]+", " ")
        .replace("-", "")
        .strip()
        .replace(" ", "_")
    )
    return clean


def get_git_hash():

    try:
        git_head_hash = (
            subprocess.check_output(["git", "rev-parse", "HEAD"])
            .strip()
            .decode("ascii")
        )
    except FileNotFoundError:
        git_head_hash = "Git hash unknown"

    return git_head_hash


def download_save(url, save_path):
    """
    Download a file that isn't zipped and save it to a given path

    Parameters
    ----------
    url : str
        Valid url to download the zip file
    save_path : str or path object
        Destination to save the file

    """

    r = requests.get(url)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(r.content)


def shift_wrap_profiles(df, offset):
    "Shift hours to a local offset and append first rows to end"

    wrap_rows = df.iloc[:offset, :]

    shifted_wrapped_df = pd.concat([df.iloc[offset:, :], wrap_rows], ignore_index=True)
    return shifted_wrapped_df


def update_dictionary(d, u):
    """
    Update keys in an existing dictionary (d) with values from u

    https://stackoverflow.com/a/32357112
    """
    for k, v in u.items():
        if isinstance(d, collections.abc.Mapping):
            if isinstance(v, collections.abc.Mapping):
                r = update_dictionary(d.get(k, {}), v)
                d[k] = r
            else:
                d[k] = u[k]
        else:
            d = {k: u[k]}
    return d


def remove_fuel_scenario_name(df, settings):
    _df = df.copy()
    scenarios = settings["eia_series_scenario_names"].keys()
    for s in scenarios:
        _df["Fuel"] = _df["Fuel"].str.replace(f"_{s}", "")

    return _df


def write_results_file(df, folder, file_name, include_index=False):
    """Write a finalized dataframe to one of the results csv files.

    Parameters
    ----------
    df : DataFrame
        Data for a single results file
    folder : Path-like
        A Path object representing the folder for a single case/scenario
    file_name : str
        Name of the file.
    include_index : bool, optional
        If pandas should include the index when writing to csv, by default False
    """
    sub_folder = folder / "Inputs"
    sub_folder.mkdir(exist_ok=True, parents=True)

    path_out = sub_folder / file_name

    df.to_csv(path_out, index=include_index)


def write_case_settings_file(settings, folder, file_name):
    """Write a finalized dictionary to YAML file.

    Parameters
    ----------
    settings : dict
        A dictionary with settings
    folder : Path-like
        A Path object representing the folder for a single case/scenario
    file_name : str
        Name of the file.
    """
    folder.mkdir(exist_ok=True, parents=True)
    path_out = folder / file_name

    # yaml = YAML(typ="unsafe")
    _settings = deepcopy(settings)
    # for key, value in _settings.items():
    #     if isinstance(value, Path):
    #         _settings[key] = str(value)
    # yaml.register_class(Path)
    # stream = file(path_out, 'w')
    with open(path_out, "w") as f:
        yaml.dump(_settings, f)


def find_centroid(gdf):
    """Find the centroid of polygons, even when in a geographic CRS

    If the crs is geographic (uses lat/lon) then it is converted to a projection before
    calculating the centroid.

    The projected CRS used here is:

    <Projected CRS: EPSG:2163>
    Name: US National Atlas Equal Area
    Axis Info [cartesian]:
    - X[east]: Easting (metre)
    - Y[north]: Northing (metre)
    Area of Use:
    - name: USA
    - bounds: (167.65, 15.56, -65.69, 74.71)
    Coordinate Operation:
    - name: US National Atlas Equal Area
    - method: Lambert Azimuthal Equal Area (Spherical)
    Datum: Not specified (based on Clarke 1866 Authalic Sphere)
    - Ellipsoid: Clarke 1866 Authalic Sphere
    - Prime Meridian: Greenwich

    Parameters
    ----------
    gdf : GeoDataFrame
        A gdf with a geometry column.

    Returns
    -------
    GeoSeries
        A GeoSeries of centroid Points.
    """

    crs = gdf.crs

    if crs.is_geographic:
        _gdf = gdf.to_crs("EPSG:2163")
        centroid = _gdf.centroid
        centroid = centroid.to_crs(crs)
    else:
        centroid = gdf.centroid

    return centroid


def regions_to_keep(settings):
    """Create a list of all IPM regions that are used in the model, either as single
    regions or as part of a user-defined model region. Also includes the aggregate
    regions defined by user.

    Parameters
    ----------
    settings : dict
        User-defined parameters from a settings YAML file with keys "model_regions" and
        "region_aggregations".

    Returns
    -------
    list
        All of the IPM regions and user defined model regions.
    """
    # Settings has a dictionary of lists for regional aggregations.
    region_agg_map = reverse_dict_of_lists(settings.get("region_aggregations"))

    # IPM regions to keep - single in model_regions plus those aggregated by the user
    keep_regions = [
        x
        for x in settings["model_regions"] + list(region_agg_map)
        if x not in region_agg_map.values()
    ]
    return keep_regions, region_agg_map


def remove_feb_29(df: pd.DataFrame) -> pd.DataFrame:
    """Remove Feb 29 from a wide format leap-year dataseries

    Parameters
    ----------
    df : pd.DataFrame
        A wide format dataframe with 8784 columns

    Returns
    -------
    pd.DataFrame
        The same dataframe but without the 24 hours in Feb 29 and only 8760 rows.
    """    
    idx_start = df.index.min()
    idx_name = df.index.name
    df["datetime"] = pd.date_range(start="2012-01-01", freq="H", periods=8784)

    df = df.loc[~((df.datetime.dt.month == 2) & (df.datetime.dt.day == 29)), :]
    df.index = range(idx_start, idx_start + 8760)
    df.index.name = idx_name

    return df.drop(columns=["datetime"])