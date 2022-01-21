"""Test functions in generation.py"""
import logging
import sqlite3

import numpy as np
import pandas as pd
import sqlalchemy
import powergenome
import pytest
from powergenome.generators import (
    fill_missing_tech_descriptions,
    gentype_region_capacity_factor,
    group_technologies,
    label_retirement_year,
    label_small_hydro,
    unit_generator_heat_rates,
    load_860m,
)
from powergenome.load_profiles import make_load_curves
from powergenome.params import DATA_PATHS
from powergenome.transmission import agg_transmission_constraints
from powergenome.util import (
    check_settings,
    load_settings,
    map_agg_region_names,
    reverse_dict_of_lists,
)

logger = logging.getLogger(powergenome.__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    # More extensive test-like formatter...
    "%(asctime)s [%(levelname)8s] %(name)s:%(lineno)s %(message)s",
    # This is the datetime format string.
    "%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)
logger.addHandler(handler)

PUDL_DB_CONN = sqlite3.connect(DATA_PATHS["test_data"] / "pudl_test_data.db")
PG_DB_CONN = sqlalchemy.create_engine(
    "sqlite:////" + str(DATA_PATHS["test_data"] / "pg_misc_tables.sqlite3")
)


@pytest.fixture(scope="module")
def generation_fuel_eia923_data():
    gen_fuel = pd.read_sql_query(
        "SELECT * FROM generation_fuel_eia923",
        PUDL_DB_CONN,
        parse_dates=["report_date"],
    )
    return gen_fuel


@pytest.fixture(scope="module")
def generators_eia860_data():
    sql = """
        SELECT *
        FROM generators_eia860
        WHERE operational_status_code = 'OP'
    """
    gens_860 = pd.read_sql_query(
        sql, PUDL_DB_CONN, parse_dates=["report_date", "planned_retirement_date"]
    )
    return gens_860


@pytest.fixture(scope="module")
def generators_entity_eia_data():
    gen_entity = pd.read_sql_query(
        "SELECT * FROM generators_entity_eia",
        PUDL_DB_CONN,
        parse_dates=["operating_date"],
    )
    return gen_entity


@pytest.fixture(scope="module")
def plant_region_map_ipm_data():
    plant_region_map = pd.read_sql_query(
        "SELECT * FROM plant_region_map_epaipm", PUDL_DB_CONN
    )
    return plant_region_map


@pytest.fixture(scope="module")
def test_settings():
    settings = load_settings(DATA_PATHS["test_data"] / "test_settings.yml")
    return settings


class MockPudlOut:
    """
    The methods in this class read pre-calculated tables from a sqlite db and return
    the expected values from pudl_out methods.
    """

    def hr_by_unit():
        "Heat rate by unit over multiple years"
        hr_by_unit = pd.read_sql_query(
            "SELECT * FROM hr_by_unit", PUDL_DB_CONN, parse_dates=["report_date"]
        )
        return hr_by_unit

    def bga():
        "Boiler generator associations with unit_id_pudl values"
        bga = pd.read_sql_query(
            "SELECT * FROM boiler_generator_assn_eia860", PUDL_DB_CONN
        )
        return bga


def test_group_technologies(generators_eia860_data, test_settings):
    df = generators_eia860_data.loc[
        generators_eia860_data.report_date.dt.year == 2018, :
    ]
    # df = df.query("report_date.dt.year==2017")
    df = df.drop_duplicates(subset=["plant_id_eia", "generator_id"])

    grouped_by_tech = group_technologies(
        df,
        test_settings.get("group_technologies"),
        test_settings.get("tech_groups", {}) or {},
        test_settings.get("regional_no_grouping", {}) or {},
    )
    techs = grouped_by_tech["technology_description"].unique()
    capacities = grouped_by_tech.groupby("technology_description")[
        test_settings["capacity_col"]
    ].sum()
    # expected_hydro_cap = 48.1
    hydro_cap = capacities["Conventional Hydroelectric"]
    expected_peaker_cap = 354.8
    # peaker_cap = capacities["Peaker"]

    assert len(df) == len(grouped_by_tech)
    assert df["capacity_mw"].sum() == grouped_by_tech["capacity_mw"].sum()
    assert "Peaker" in techs
    # assert np.allclose(hydro_cap, expected_hydro_cap)
    # assert np.allclose(peaker_cap, expected_peaker_cap)


def test_fill_missing_tech_descriptions(generators_eia860_data):
    filled = fill_missing_tech_descriptions(generators_eia860_data)

    assert len(generators_eia860_data) == len(
        filled.dropna(subset=["technology_description"])
    )


def test_label_small_hyro(
    generators_eia860_data, test_settings, plant_region_map_ipm_data
):
    region_agg_map = reverse_dict_of_lists(test_settings.get("region_aggregations", {}))
    model_region_map_df = map_agg_region_names(
        df=plant_region_map_ipm_data,
        region_agg_map=region_agg_map,
        original_col_name="region",
        new_col_name="model_region",
    )
    df = pd.merge(
        generators_eia860_data, model_region_map_df, on="plant_id_eia", how="left"
    )
    logger.info(df[["plant_id_eia", "technology_description", "model_region"]].head())

    # df["model_region"] = df["region"].map(reverse_dict_of_lists)

    df = label_small_hydro(df, test_settings, by=["plant_id_eia", "report_date"])
    print(df.query("plant_id_eia==34"))
    logger.info(df["technology_description"].unique())

    assert "Small Hydroelectric" in df["technology_description"].unique()
    assert np.allclose(
        df.loc[df.technology_description == "Small Hydroelectric", "capacity_mw"], 12.1
    )


def test_label_retirement_year(
    generators_eia860_data, generators_entity_eia_data, test_settings
):
    gens = pd.merge(
        generators_eia860_data,
        generators_entity_eia_data,
        on=["plant_id_eia", "generator_id"],
        how="left",
    )
    df = label_retirement_year(gens, test_settings)
    print(df)

    assert df.loc[df["retirement_year"].isnull(), :].empty is True


def test_unit_generator_heat_rates(data_years=[2018]):
    hr_df = unit_generator_heat_rates(MockPudlOut, data_years)

    assert hr_df.empty is False
    assert "heat_rate_mmbtu_mwh" in hr_df.columns
    assert np.allclose(
        hr_df.query("plant_id_eia==117 & unit_id_pudl == 2")[
            "heat_rate_mmbtu_mwh"
        ].values,
        [7.805624],
    )


def test_load_860m(test_settings):
    eia_860m = load_860m(test_settings)
    test_settings["eia_860m_fn"] = None
    eia_860m = load_860m(test_settings)


def test_agg_transmission_constraints(test_settings):
    agg_transmission_constraints(PG_DB_CONN, test_settings)


def test_demand_curve(test_settings):
    make_load_curves(PG_DB_CONN, test_settings)


def test_check_settings(test_settings):
    check_settings(test_settings, PG_DB_CONN)


# def test_gentype_region_capacity_factor(plant_region_map_ipm_data, test_settings):

#     df = gentype_region_capacity_factor(
#         PUDL_DB_CONN, plant_region_map_ipm_data, test_settings
#     )
#     print(df.technology.unique())
#     assert "Peaker" in df.technology.unique()
