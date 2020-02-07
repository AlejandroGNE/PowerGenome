"Functions specific to GenX outputs"

from itertools import product
import logging
import pandas as pd

from powergenome.external_data import load_policy_scenarios, load_demand_segments
from powergenome.load_profiles import make_distributed_gen_profiles
from powergenome.time_reduction import kmeans_time_clustering
from powergenome.util import load_settings

logger = logging.getLogger(__name__)


def add_emission_policies(transmission_df, settings, DistrZones=None):
    """Add emission policies to the transmission dataframe

    Parameters
    ----------
    transmission_df : DataFrame
        Zone to zone transmission constraints
    settings : dict
        User-defined parameters from a settings file. Should have keys of `input_folder`
        (a Path object of where to find user-supplied data) and
        `emission_policies_fn` (the file to load).
    DistrZones : [type], optional
        Placeholder setting, by default None

    Returns
    -------
    DataFrame
        The emission policies provided by user next to the transmission constraints.
    """

    model_year = settings["model_year"]
    case_id = settings["case_id"]

    policies = load_policy_scenarios(settings)
    year_case_policy = policies.loc[(case_id, model_year), :]

    zones = settings["model_regions"]
    zone_num_map = {
        zone: f"z{number + 1}" for zone, number in zip(zones, range(len(zones)))
    }

    zone_cols = ["Region description", "Network_zones", "DistrZones"] + list(
        policies.columns
    )
    zone_df = pd.DataFrame(columns=zone_cols)
    zone_df["Region description"] = zones
    zone_df["Network_zones"] = zone_df["Region description"].map(zone_num_map)

    if DistrZones is None:
        zone_df["DistrZones"] = 0

    # Add code here to make DistrZones something else!
    # If there is only one region, assume that the policy is applied across all regions.
    if isinstance(year_case_policy, pd.Series):
        logger.info(
            "Only one zone was found in the emissions policy file."
            " The same emission policies are being applied to all zones."
        )
        for col, value in year_case_policy.iteritems():
            if col == "CO_2_Max_Mtons":
                zone_df.loc[:, col] = 0
                zone_df.loc[0, col] = value
            else:
                zone_df.loc[:, col] = value
    else:
        for region, col in product(
            year_case_policy["region"].unique(), year_case_policy.columns
        ):
            zone_df.loc[
                zone_df["Region description"] == region, col
            ] = year_case_policy.loc[year_case_policy.region == region, col].values[0]

    zone_df = zone_df.drop(columns="region")

    network_df = pd.concat([zone_df, transmission_df.reset_index()], axis=1)

    return network_df


def make_genx_settings_file(pudl_engine, settings):
    """Make a copy of the GenX settings file for a specific case.

    This assumes that there is a base-level GenX settings file with parameters that
    stay constant across all cases.

    Parameters
    ----------
    pudl_engine : sqlalchemy.Engine
        A sqlalchemy connection for use by pandas to access IPM load profiles. These
        load profiles are needed when DG is calculated as a fraction of load.
    settings : dict
        User-defined parameters from a settings file. Should have keys of `model_year`
        `case_id`, 'case_name', `input_folder` (a Path object of where to find
        user-supplied data), `emission_policies_fn`, 'distributed_gen_profiles_fn'
        (the files to load in other functions), and 'genx_settings_fn'.

    Returns
    -------
    dict
        Dictionary of settings for a GenX run
    """

    model_year = settings["model_year"]
    case_id = settings["case_id"]
    case_name = settings["case_name"]

    genx_settings = load_settings(settings["genx_settings_fn"])
    policies = load_policy_scenarios(settings)
    year_case_policy = policies.loc[(case_id, model_year), :]
    dg_generation = make_distributed_gen_profiles(pudl_engine, settings)
    total_dg_gen = dg_generation.sum().sum()

    if isinstance(year_case_policy, pd.DataFrame):
        year_case_policy = year_case_policy.sum()

    if float(year_case_policy["CO_2_Max_Mtons"]) > 0:
        genx_settings["CO2Cap"] = 2
    else:
        genx_settings["CO2Cap"] = 0

    if float(year_case_policy["RPS"]) > 0:
        # print(total_dg_gen)
        # print(year_case_policy["RPS"])
        if len(policies.loc[(case_id, model_year), "region"].unique()) > 1:
            genx_settings["RPS"] = 2
            genx_settings["RPS_Adjustment"] = 0
        else:
            genx_settings["RPS"] = 3
            genx_settings["RPS_Adjustment"] = float(
                (1 - year_case_policy["RPS"]) * total_dg_gen
            )
    else:
        genx_settings["RPS"] = 0
        genx_settings["RPS_Adjustment"] = 0

    if float(year_case_policy["CES"]) > 0:
        if len(policies.loc[(case_id, model_year), "region"].unique()) > 1:
            genx_settings["CES"] = 2
            genx_settings["CES_Adjustment"] = 0
        else:
            genx_settings["CES"] = 3
            genx_settings["CES_Adjustment"] = float(
                (1 - year_case_policy["CES"]) * total_dg_gen
            )
    else:
        genx_settings["CES"] = 0
        genx_settings["CES_Adjustment"] = 0

    genx_settings["case_id"] = case_id
    genx_settings["case_name"] = case_name
    genx_settings["year"] = str(model_year)

    return genx_settings


def reduce_time_domain(
    resource_profiles, load_profiles, settings, variable_resources_only=True
):

    demand_segments = load_demand_segments(settings)

    days = settings["time_domain_days_per_period"]
    time_periods = settings["time_domain_periods"]
    include_peak_day = settings["include_peak_day"]
    load_weight = settings["demand_weight_factor"]

    results, rep_point, cluster_weight = kmeans_time_clustering(
        resource_profiles=resource_profiles,
        load_profiles=load_profiles,
        days_in_group=days,
        num_clusters=time_periods,
        include_peak_day=include_peak_day,
        load_weight=load_weight,
        variable_resources_only=variable_resources_only,
    )

    reduced_resource_profile = results["resource_profiles"]
    reduced_load_profile = results["load_profiles"]

    time_index = pd.Series(data=reduced_load_profile.index + 1, name="Time_index")
    sub_weights = pd.Series(
        data=[x * (days * 24) for x in results["ClusterWeights"]], name="Sub_Weights"
    )
    hours_per_period = pd.Series(data=[days * 24], name="Hours_per_period")
    subperiods = pd.Series(data=[time_periods], name="Subperiods")
    reduced_load_output = pd.concat(
        [
            demand_segments,
            subperiods,
            hours_per_period,
            sub_weights,
            time_index,
            reduced_load_profile,
        ],
        axis=1,
    )

    return reduced_resource_profile, reduced_load_output
