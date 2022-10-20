"""
Load data from EIA's Open Data API. Requires an api key, which should be included in a
.env file (/powergenome/.env) with the format EIA_API_KEY=YOUR_API_KEY
"""

from itertools import product
import logging
import operator
from typing import Union

import pandas as pd
import requests

from powergenome.params import SETTINGS, DATA_PATHS
from powergenome.price_adjustment import inflation_price_adjustment
from powergenome.util import reverse_dict_of_lists

logger = logging.getLogger(__name__)

numeric = Union[int, float]


def load_aeo_series(
    aeo_year: int, aeo_scenario: str, series_id: str, api_key: str, columns: list = None
) -> pd.DataFrame:
    """Load EIA AEO data either from file (if it exists) or from v2 of the API.

    Data are saved to disk if loaded from the API.

    Parameters
    ----------
    aeo_year : int
        Year of AEO data
    aeo_scenario : str
        Name of the AEO scenario
    series_id : str
        ID of the series
    api_key : str
        User API key for EIA open data portal
    columns : list, optional
        Data columns to include in the output, by default None (return all)

    Returns
    -------
    pd.DataFrame
        AEO scenario data from EIA's open data API

    Raises
    ------
    requests.HTTPError
        The API request failed for some reason.
    """
    data_dir = DATA_PATHS["eia"] / "open_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    fn = f"{aeo_year}_{aeo_scenario.lower()}_{series_id}.csv"
    if not (data_dir / fn).exists():
        url = f"https://api.eia.gov/v2/aeo/{aeo_year}/data/?api_key={api_key}&facets[scenario][]={aeo_scenario.lower()}&facets[seriesId][]={series_id}&data[]=value"
        r = requests.get(url)
        if int(r.status_code) not in (200, 201):
            raise requests.HTTPError(r.json())

        try:
            df = pd.DataFrame(r.json()["response"]["data"])
        except KeyError:
            print(
                "There was an error creating a dataframe from your EIA AEO data request. "
                f"The constructed series ID is {series_id}. Check to make sure it looks "
                "correct. The data returned from EIA's API is: \n"
                f"{r.json()}"
            )
        df.to_csv(data_dir / fn, index=False)
    else:
        df = pd.read_csv(data_dir / fn)
    if columns:
        df = df[columns]
    return df


def fetch_fuel_prices(settings: dict, inflate_price: bool = True) -> pd.DataFrame:
    """
    Get EIA AEO fuel prices for all regions, fuel types, and scenarios (series IDs)
    included in the settings.

    Parameters
    ----------
    settings : dict
        Should include the following keys:
            eia_aeo_year (int)
            eia_series_region_names (list)
            eia_series_fuel_names (list)
            eia_series_scenario_names (list)
    inflate_price: bool
        If True, adjust the AEO prices to the year "target_usd_year" from the settings.
        If False, do not adjust the AEO prices. Requires the additional settings keys
        "target_usd_year" and "aeo_fuel_usd_year".

    Returns
    -------
    pd.DataFrame
        All fuel price data from AEO for the product of regions, fuels, and scenarios
        included in the settings dictionary.

    Raises
    ------
    KeyError
        The settings parameter "eia_series_scenario_names" is missing.
    KeyError
        The AEO data year is not specified by either of the settings parameters
        "fuel_eia_aeo_year" or "eia_aeo_year".
    TypeError
        The parameter value of "fuel_eia_aeo_year" or "eia_aeo_year" is not an integer.

    Examples
    --------
    Prepare the settings dictionary
    >>> settings = {}
    >>> settings["eia_aeo_year"] = 2020
    >>> settings["aeo_fuel_usd_year"] = 2019
    >>> settings["eia_series_scenario_names"] = {"reference": "REF2020"}
    >>> settings["eia_series_fuel_names"] = {"coal": "STC"}
    >>> settings["eia_series_region_names"] = {"mountain": "MTN"}

    Find the fuel cost with inflating costs.

    >>> fuel_price = fetch_fuel_prices(settings, inflate_price=False)
    >>> print(fuel_price.head())
       year     price  fuel    region   scenario           full_fuel_name
    0  2050  1.501850  coal  mountain  reference  mountain_reference_coal
    1  2049  1.488098  coal  mountain  reference  mountain_reference_coal
    2  2048  1.508208  coal  mountain  reference  mountain_reference_coal
    3  2047  1.506809  coal  mountain  reference  mountain_reference_coal
    4  2046  1.497366  coal  mountain  reference  mountain_reference_coal

    If either of the keys "target_usd_year" or "aeo_fuel_usd_year" is missing, fuel prices
    cannot be inflated.

    >>> fuel_price = fetch_fuel_prices(settings)
    ************
    Unable to inflate fuel prices. Check your settings file to ensure the keys
    "target_usd_year" and "aeo_fuel_usd_year" exist and are valid integers.
    ************
    """
    API_KEY = SETTINGS["EIA_API_KEY"]

    if settings.get("fuel_eia_aeo_year"):
        aeo_year = settings.get("fuel_eia_aeo_year")
    else:
        aeo_year = settings.get("eia_aeo_year")

    if not aeo_year:
        raise KeyError(
            "The parameter 'fuel_eia_aeo_year' is not in your settings files. This is a "
            "required settings parameter when using fuel price data from EIA's AEO."
        )
    if not isinstance(aeo_year, int):
        raise TypeError(
            "The settings parameter 'fuel_eia_aeo_year' (or 'eia_aeo_year') must be an "
            f"integer, representing the AEO data year. Your parameter is {aeo_year}."
        )
    if not settings.get("eia_series_region_names"):
        logger.warning(
            "EIA fuel region names were not found in the settings ('eia_series_region_names'). "
            "Applying default values."
        )
        settings["eia_series_region_names"] = dict(
            mountain="MTN",
            pacific="PCF",
            west_south_central="WSC",
            east_south_central="ESC",
            south_atlantic="SOATL",
            west_north_central="WNC",
            east_north_central="ENC",
            middle_atlantic="MDATL",
            new_england="NEENGL",
        )
    if not settings.get("eia_series_fuel_names"):
        settings["eia_series_fuel_names"] = {
            "coal": "STC",
            "naturalgas": "NG",
            "distillate": "DFO",
            "uranium": "U",
        }
        logger.warning(
            "EIA fuel names were not found in the settings ('eia_series_fuel_names'). "
            "Applying default values:\n\n"
            f"{settings['eia_series_fuel_names']}"
        )
    if not settings.get("eia_series_scenario_names"):
        raise KeyError(
            "The settings parameter 'eia_series_scenario_names' is missing. This mapping "
            "of AEO scenario API names (e.g. REF2020) to plain english is required."
        )

    fuel_price_cases = product(
        settings.get("eia_series_region_names", {}).items(),
        settings.get("eia_series_fuel_names", {}).items(),
        settings.get("eia_series_scenario_names", {}).items(),
    )
    if not aeo_year or not fuel_price_cases:
        w = False
        for f in ["coal", "naturalgas", "distillate", "uranium"]:
            if f in settings.get("tech_fuel_map", {}).values():
                w = True
        if w:
            logger.warning(
                "Unable to get AEO fuel prices due to missing settings parameter 'eia_aeo_year', "
                "'eia_series_region_names', 'eia_series_fuel_names', or 'eia_series_scenario_names'. "
                "You have listed at least one AEO fuel in your settings 'tech_fuel_map' "
                "parameter, but no prices for these fuels are being included."
            )
        return pd.DataFrame(
            columns=["fuel", "region", "scenario", "full_fuel_name", "year"]
        )
    df_list = []
    for region, fuel, scenario in fuel_price_cases:
        region_name, region_series = region
        fuel_name, fuel_series = fuel
        scenario_name, scenario_series = scenario

        SERIES_ID = f"prce_real_elep_NA_{fuel_series.lower()}_NA_{region_series.lower()}_y13dlrpmmbtu"

        df = (
            load_aeo_series(
                aeo_year=aeo_year,
                aeo_scenario=scenario_series.lower(),
                series_id=SERIES_ID,
                api_key=API_KEY,
                columns=["period", "value"],
            )
            .rename(columns={"period": "year", "value": "price"})
            .sort_values("year")
            .reset_index(drop=True)
        )
        df["fuel"] = fuel_name
        df["region"] = region_name
        df["scenario"] = scenario_name
        df["full_fuel_name"] = df.region + "_" + df.scenario + "_" + df.fuel
        df["year"] = df["year"].astype(int)

        df_list.append(df)

    final = pd.concat(df_list, ignore_index=True)

    if inflate_price:
        try:
            fuel_price_base_year = settings["aeo_fuel_usd_year"]
            fuel_price_target_year = settings["target_usd_year"]
            final.loc[:, "price"] = inflation_price_adjustment(
                price=final.loc[:, "price"],
                base_year=fuel_price_base_year,
                target_year=fuel_price_target_year,
            )
        except (KeyError, TypeError):
            logger.warning(
                """
    ************
    Unable to inflate fuel prices. Check your settings file to ensure the keys
    "target_usd_year" and "aeo_fuel_usd_year" are valid integers.
    ************
                """
            )

    return final


def modify_fuel_prices(
    prices: pd.DataFrame,
    aeo_fuel_region_map: dict,
    regional_fuel_adjustments: dict = None,
) -> pd.DataFrame:
    """Modify the AEO fuel prices by model region or fuel within a model region.

    Parameters
    ----------
    prices : pd.DataFrame
        Fuel prices from AEO, with columns ['year', 'price', 'fuel', 'region', 'scenario',
        'full_fuel_name']
    aeo_fuel_region_map : dict
        Mapping of AEO census division fuel names to lists of model regions
    regional_fuel_adjustments : dict, optional
        Modifications of fuel prices by region or fuel within region, by default None

    Returns
    -------
    pd.DataFrame
        Full input dataframe with modified copies for model regions and fuels specified
        in `regional_fuel_adjustments`.

    Raises
    ------
    KeyError
        The required parameter 'aeo_fuel_region_map' is missing
    KeyError
        One or more model regions having fuel prices modified is not in `aeo_fuel_region_map`
    KeyError
        Invalid operator type
    KeyError
        Invalid fuel name
    KeyError
        Invalid operator type
    TypeError
        Fuel price modifiers are not a list or a dictionary of lists
    """

    if not regional_fuel_adjustments:
        return prices

    if not aeo_fuel_region_map:
        raise KeyError("The required parameter 'aeo_fuel_region_map' is missing.")

    allowed_operators = ["add", "mul", "truediv", "sub"]
    model_regions = list(regional_fuel_adjustments)
    model_aeo_region_map = reverse_dict_of_lists(aeo_fuel_region_map)
    if not all(r in model_aeo_region_map for r in model_regions):
        raise KeyError(
            "All model regions listed in the settings parameter 'regional_fuel_adjustments' "
            "should also be included in `aeo_fuel_region_map`. One or more regions was "
            "not found."
        )

    df_list = []
    for region, adj in regional_fuel_adjustments.items():
        aeo_region = model_aeo_region_map[region]
        if isinstance(adj, list):
            op, op_value = adj
            if op not in allowed_operators:
                raise KeyError(
                    f"The regional fuel price adjustment for {region} needs a valid "
                    f"operator from the list\n{allowed_operators}\n"
                    "in the format [<operator>, <value>].\n"
                )
            f = operator.attrgetter(op)
            df = prices.loc[prices["region"] == aeo_region, :]
            df.loc[:, "region"] = region
            df.loc[:, "price"] = f(operator)(df["price"], op_value)
            df.loc[:, "full_fuel_name"] = df["full_fuel_name"].str.replace(
                aeo_region, region
            )
            df_list.append(df)
        elif isinstance(adj, dict):
            for fuel, op_list in adj.items():
                if fuel not in prices["fuel"].unique():
                    raise KeyError(
                        f"The fuel '{fuel}' is listed under the region {region} in your settings "
                        "parameter 'regional_fuel_adjustments'. There was no AEO fuel "
                        "price fetched for this fuel so it cannot be modified."
                    )
                op, op_value = op_list
                if op not in allowed_operators:
                    raise KeyError(
                        f"The regional fuel price adjustment for '{fuel}' in {region} "
                        f"needs to be an operator from the list {allowed_operators}. "
                        f"You supplied '{op}', which is not a valid operator."
                    )
                f = operator.attrgetter(op)
                df = prices.loc[
                    (prices["region"] == aeo_region) & (prices["fuel"] == fuel.lower()),
                    :,
                ]
                df.loc[:, "region"] = region
                df.loc[:, "price"] = f(operator)(df["price"], op_value)
                df.loc[:, "full_fuel_name"] = df["full_fuel_name"].str.replace(
                    aeo_region, region
                )
                df_list.append(df)
        else:
            raise TypeError(
                "Fuel price modifiers in the settings parameter 'regional_fuel_adjustments' "
                "must be a list of the form '[<op>, <value>]', or a similar list for a "
                "specific fuel. "
                f"Your value look like '{adj}' for region '{region}'."
            )

    mod_prices = pd.concat([prices] + df_list, ignore_index=True, sort=False)

    return mod_prices


def add_user_fuel_prices(settings: dict, df: pd.DataFrame = None) -> pd.DataFrame:
    """Add user fuel prices to a dataframe of user prices from AEO (or elsewhere)

    Parameters
    ----------
    settings : dict
        If adding user prices, should have the key "user_fuel_price" with value of a
        dictionary matching user fuel names and prices. Prices can either be a single
        price for all regions or a price per region. For example this shows biomass with
        different prices in two regions and ZCF with the same price in all regions:

        settings["user_fuel_price"] = {
            "biomass": {"SC_VACA": 10, "PJM_DOM": 5},
            "ZCF": 15
        }

        If the keys "target_usd_year" and "user_fuel_usd_year" are also included, fuel
        prices will be corrected to the correct USD year. "user_fuel_usd_year" should
        be a dictionary with fuel name: USD year pairings. Only fuels included in this
        dictionary will have their prices changed to the target USD year.
    df : pd.DataFrame, optional
        A dataframe with fuel prices from AEO (or elsewhere), by default None. Should
        have columns ["year", "price", "fuel", "region", "scenario", "full_fuel_name"]

    Returns
    -------
    pd.DataFrame
        The combined dataframes of user prices and the other price dataframe provided
        as input. Columns are ["year", "price", "fuel", "region", "scenario", "full_fuel_name"].
    """

    if not settings.get("user_fuel_price"):
        if df is not None:
            return df
    cols = ["year", "price", "fuel", "region", "scenario", "full_fuel_name"]
    if df is not None and not df.empty:
        years = df["year"].unique()
    else:
        years = range(2020, 2051)
    fuel_data = {c: [] for c in cols}

    for fuel, val in settings["user_fuel_price"].items():
        if isinstance(val, dict):
            for region, price in val.items():
                fuel_name = f"{region}_{fuel}"
                fuel_data["year"].extend(years)
                fuel_data["price"].extend([price] * len(years))
                fuel_data["fuel"].extend([fuel] * len(years))
                fuel_data["region"].extend([region] * len(years))
                fuel_data["scenario"].extend(["user"] * len(years))
                fuel_data["full_fuel_name"].extend([fuel_name] * len(years))
        else:
            fuel_data["year"].extend(years)
            fuel_data["price"].extend([val] * len(years))
            fuel_data["fuel"].extend([fuel] * len(years))
            fuel_data["region"].extend([""] * len(years))
            fuel_data["scenario"].extend(["user"] * len(years))
            fuel_data["full_fuel_name"].extend([fuel] * len(years))

    user_fuel_price = pd.DataFrame(fuel_data)
    if settings.get("target_usd_year"):
        for fuel, year in (settings.get("user_fuel_usd_year", {}) or {}).items():
            user_fuel_price.loc[
                user_fuel_price["fuel"] == fuel, "price"
            ] = inflation_price_adjustment(
                user_fuel_price.loc[user_fuel_price["fuel"] == fuel, "price"],
                year,
                settings["target_usd_year"],
            )
    if df is not None:
        user_fuel_price = pd.concat([df, user_fuel_price])
    return user_fuel_price


def get_aeo_load(
    region: str,
    aeo_year: Union[str, numeric],
    scenario_series: str,
    sector: str = "ELEP",
) -> pd.DataFrame:
    """Find the electricity demand in a single AEO region. Use EIA API if data has not
    been previously saved.

    Parameters
    ----------
    region : str
        Short name of the AEO region
    aeo_year : Union[str, numeric]
        AEO data year
    scenario_series : str
        Short name of the AEO scenario

    Returns
    -------
    pd.DataFrame
        The demand data for a single region.

    Examples
    --------
    >>> texas_load = get_aeo_load("TRE", 2020, "REF2020")
    >>> print(texas_load.head())
       year      demand
    0  2050  489.009247
    1  2049  483.176544
    2  2048  477.624481
    3  2047  472.314972
    4  2046  466.875671
    """
    API_KEY = SETTINGS["EIA_API_KEY"]

    SERIES_ID = f"cnsm_NA_{sector.lower()}_NA_elc_NA_{region.lower()}_blnkwh"

    df = (
        load_aeo_series(
            aeo_year=aeo_year,
            aeo_scenario=scenario_series,
            series_id=SERIES_ID,
            api_key=API_KEY,
            columns=["period", "value"],
        )
        .rename(columns={"period": "year", "value": "demand"})
        .sort_values("year")
        .reset_index(drop=True)
    )
    df["year"] = df["year"].astype(int)

    return df
