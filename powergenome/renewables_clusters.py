import glob
import json
import logging
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Union

import numpy as np
import pandas as pd
import pyarrow
import pyarrow.parquet as pq
import scipy.cluster.hierarchy

logger = logging.getLogger(__name__)

CAPACITY = "mw"
WEIGHT = CAPACITY
MEANS = [
    "lcoe",
    "interconnect_annuity",
    "offshore_spur_miles",
    "spur_miles",
    "tx_miles",
    "site_substation_spur_miles",
    "substation_metro_tx_miles",
    "site_metro_spur_miles",
]
UNIQUES = ["ipm_region", "metro_id"]
SUMS = ["area", CAPACITY]
NREL_ATB_TECHNOLOGY_MAP = {
    ("utilitypv", None): {"technology": "utilitypv"},
    ("landbasedwind", None): {"technology": "landbasedwind"},
    ("offshorewind", None): {"technology": "offshorewind"},
    **{
        ("offshorewind", f"otrg{x}"): {
            "technology": "offshorewind",
            "turbine_type": "fixed",
        }
        for x in range(1, 6)
    },
    **{
        ("offshorewind", f"otrg{x}"): {
            "technology": "offshorewind",
            "turbine_type": "floating",
        }
        for x in range(6, 16)
    },
}


def _normalize(x: Optional[str]) -> Optional[str]:
    """
    Normalize string to lowercase and no whitespace.

    Examples
    --------
    >>> _normalize('Offshore Wind')
    'offshorewind'
    >>> _normalize('OffshoreWind')
    'offshorewind'
    >>> _normalize(None) is None
    True
    """
    if not x:
        return x
    return re.sub(r"\s+", "", x.lower())


def map_nrel_atb_technology(tech: str, detail: str = None) -> Dict[str, Any]:
    """
    Map NREL ATB technology to resource groups.

    Parameters
    ----------
    tech
        Technology.
    detail
        Technology detail.

    Returns
    -------
    dict
        Key, value pairs identifying one or more resource groups.

    Examples
    --------
    >>> map_nrel_atb_technology('UtilityPV', 'LosAngeles')
    {'technology': 'utilitypv'}
    >>> map_nrel_atb_technology('LandbasedWind', 'LTRG1')
    {'technology': 'landbasedwind'}
    >>> map_nrel_atb_technology('OffShoreWind')
    {'technology': 'offshorewind'}
    >>> map_nrel_atb_technology('OffShoreWind', 'OTRG3')
    {'technology': 'offshorewind', 'turbine_type': 'fixed'}
    >>> map_nrel_atb_technology('OffShoreWind', 'OTRG7')
    {'technology': 'offshorewind', 'turbine_type': 'floating'}
    >>> map_nrel_atb_technology('Unknown')
    {}
    """
    tech = _normalize(tech)
    detail = _normalize(detail)
    group = {}
    for k, v in NREL_ATB_TECHNOLOGY_MAP.items():
        if (tech == k[0] or not k[0]) and (detail == k[1] or not k[1]):
            group.update(v)
    return group


class Table:
    """
    Cached interface for tabular data.

    Supports parquet and csv formats.

    Parameters
    ----------
    path
        Path to dataset.
    df
        In-memory dataframe.

    Attributes
    ----------
    path : Union[str, os.PathLike]
        Path to the dataset.
    df : pd.DataFrame
        Cached dataframe.
    format : str
        Dataset format ('parquet' or 'csv'), or `None` if in-memory only.
    columns : list
        Dataset column names.

    Raises
    ------
    ValueError
        Missing either path or dataframe.
    ValueError
        Dataframe columns are not all strings.

    Examples
    --------
    In-memory dataframe:

    >>> df = pd.DataFrame({'id': [1, 2], 'x': [10, 20]})
    >>> table = Table(df = df)
    >>> table.format is None
    True
    >>> table.columns
    ['id', 'x']
    >>> table.read()
       id   x
    0   1  10
    1   2  20
    >>> table.read(columns=['id'])
       id
    0   1
    1   2
    >>> table.clear()
    >>> table.df is not None
    True

    File dataset (csv):

    >>> import tempfile
    >>> fp = tempfile.NamedTemporaryFile()
    >>> df.to_csv(fp.name, index=False)
    >>> table = Table(path = fp.name)
    >>> table.format
    'csv'
    >>> table.columns
    ['id', 'x']
    >>> table.read(cache=False)
       id   x
    0   1  10
    1   2  20
    >>> table.df is None
    True
    >>> table.read(columns=['id'], cache=True)
       id
    0   1
    1   2
    >>> table.df is not None
    True
    >>> table.clear()
    >>> table.df is None
    True
    >>> fp.close()
    """

    def __init__(
        self, path: Union[str, os.PathLike] = None, df: pd.DataFrame = None
    ) -> None:
        self.path = path
        self.df = df
        if df is not None:
            if any(not isinstance(x, str) for x in df.columns):
                raise ValueError("Dataframe columns are not all strings")
        self.format = None
        self._dataset = None
        self._columns = None
        if path is not None:
            try:
                self._dataset = pq.ParquetDataset(path)
                self._columns = self._dataset.schema.names
                self.format = "parquet"
            except pyarrow.lib.ArrowInvalid:
                # Assume CSV file
                self.format = "csv"
        if path is None and df is None:
            raise ValueError("Mising either path to tabular data or a pandas DataFrame")

    @property
    def columns(self) -> list:
        if self.df is not None:
            return list(self.df.columns)
        if self._columns is None:
            if self.format == "csv":
                self._columns = pd.read_csv(self.path, nrows=0).columns
        return list(self._columns)

    def read(self, columns: Iterable = None, cache: bool = None) -> pd.DataFrame:
        """
        Read data from memory or from disk.

        Parameters
        ----------
        columns
            Names of column to read. If `None`, all columns are read. 
        cache
            Whether to cache the full dataset in memory. If `None`,
            the dataset is cached if `columns` is `None`, and not otherwise.

        Returns
        -------
        pd.DataFrame
            Data as a dataframe.
        """
        if self.df is not None:
            return self.df[columns] if columns is not None else self.df
        if cache is None:
            cache = columns is None
        read_columns = None if cache else columns
        if self.format == "csv":
            df = pd.read_csv(self.path, usecols=read_columns)
        elif self.format == "parquet":
            df = self._dataset.read(columns=read_columns).to_pandas()
        if cache:
            self.df = df
        return df[columns] if columns is not None else df

    def clear(self) -> None:
        """
        Clear the dataset cache.

        Only applies if :attr:`path` is set so that the dataset can be reread from file.
        """
        if self.path is not None:
            self.df = None


class ResourceGroup:
    """
    Group of resources sharing common attributes.

    Parameters
    ----------
    group
        Group metadata.

        - `technology` : str
          Resource type ('utilitypv', 'landbasedwind', or 'offshorewind').
        - `existing` : bool
          Whether resources are new (`False`, default) or existing (`True`).
        - `tree` : str, optional
          The name of the resource metadata attribute by
          which to differentiate between multiple precomputed hierarchical trees.
          Defaults to `None` (resource group does not represent hierarchical trees).
        - `metadata` : str, optional
          Relative path to resource metadata dataset (optional if `metadata` is `None`).
        - `profiles` : str, optional
          Relative path to resource profiles dataset (optional if `profiles` is `None`).
        - ... and any additional (optional) keys.

    metadata
        Resource metadata, with one resource per row.

        - `id`: int
          Resource identifier, unique within the group.
        - `ipm_region` : str
          IPM region to which the resource delivers power.
        - `mw` : float
          Maximum resource capacity in MW.
        - `lcoe` : float, optional
          Levelized cost of energy, used to guide the selection
          (from lowest to highest) and clustering (by nearest) of resources.
          If missing, selection and clustering is by largest and nearest `mw`.

        Resources representing hierarchical trees (see `group.tree`)
        require additional attributes.
    
        - `parent_id` : int
          Identifier of the resource formed by clustering this resource with the one
          other resource with the same `parent_id`.
          Only resources with `level` of 1 have no `parent_id`.
        - `level` : int
          Level of tree where the resource first appears, from `m`
          (the number of resources at the base of the tree), to 1.
        - `[group.tree]` : Any
          Each unique value of this grouping attribute represents a precomputed
          hierarchical tree. When clustering resources, every tree is traversed to its
          crown before the singleton resources from the trees are clustered together.
        
        The following resource attributes (all float) are propagaged as:

        - weighted means (weighted by `mw`):

            - `lcoe`
            - `interconnect_annuity`
            - `tx_miles`
            - `spur_miles`
            - `offshore_spur_miles`
            - `site_substation_spur_miles`
            - `substation_metro_tx_miles`
            - `site_metro_spur_miles`
        
        - sums:

            - `mw`
            - `area`
        
        - uniques:

            - `ipm_region`
            - `metro_id`
    
    profiles
        Variable resource capacity profiles with normalized capacity factors
        (from 0 to 1) for every hour of the year (either 8760 or 8784 for a leap year).
        Each profile must be a column whose name matches the resource `metadata.id`.
    path
        Directory relative to which the file paths `group.metadata` and `group.profiles`
        should be read.

    Attributes
    ----------
    group : Dict[str, Any]
    metadata : Table
        Cached interface to resource metadata.
    profiles : Table
        Cached interface to resource profiles.

    Examples
    --------
    >>> group = {'technology': 'utilitypv'}
    >>> metadata = pd.DataFrame({'id': [0, 1], 'ipm_region': ['A', 'A'], 'mw': [1, 2]})
    >>> profiles = pd.DataFrame({'0': np.full(8784, 0.1), '1': np.full(8784, 0.4)})
    >>> rg = ResourceGroup(group, metadata, profiles)
    >>> rg.test_metadata()
    >>> rg.test_profiles()
    >>> rg.get_clusters(max_clusters=1)
           ipm_region  mw
    (1, 0)          A   3
    >>> rg.get_cluster_profiles(ids=[(0, 1)])
    array([[0.3, 0.3, 0.3, ..., 0.3, 0.3, 0.3]])
    """

    def __init__(
        self,
        group: Dict[str, Any],
        metadata: pd.DataFrame = None,
        profiles: pd.DataFrame = None,
        path: str = ".",
    ) -> None:
        self.group = {"existing": False, "tree": None, **group.copy()}
        for key in ["metadata", "profiles"]:
            if self.group.get(key):
                # Convert relative paths (relative to group file) to absolute paths
                self.group[key] = os.path.abspath(os.path.join(path, self.group[key]))
        required = ["technology"]
        if metadata is None:
            required.append("metadata")
        if profiles is None:
            required.append("profiles")
        missing = [key for key in required if not self.group.get(key)]
        if missing:
            raise ValueError(
                f"Group metadata missing required keys {missing}: {self.group}"
            )
        self.metadata = Table(df=metadata, path=self.group.get("metadata"))
        self.profiles = Table(df=profiles, path=self.group.get("profiles"))

    @classmethod
    def from_json(cls, path: Union[str, os.PathLike]) -> "ResourceGroup":
        """
        Build from JSON file.

        Parameters
        ----------
        path
            Path to JSON file.
        """
        with open(path, mode="r") as fp:
            group = json.load(fp)
        return cls(group, path=os.path.dirname(path))

    def test_metadata(self) -> None:
        """
        Test that `:attr:metadata` is valid.

        Raises
        ------
        ValueError
            Resource metadata missing required keys.
        """
        columns = self.metadata.columns
        required = ["ipm_region", "id", "mw"]
        if self.group.get("tree"):
            required.extend(["parent_id", "level", self.group["tree"]])
        missing = [key for key in required if key not in columns]
        if missing:
            raise ValueError(f"Resource metadata missing required keys {missing}")

    def test_profiles(self) -> None:
        """
        Test that `:attr:profiles` is valid.

        Raises
        ------
        ValueError
            Resource profiles column names do not match resource identifiers.
        ValueError
            Resource profiles are not either 8760 or 8784 elements.
        """
        # Cast identifiers to string to match profile columns
        ids = self.metadata.read(columns=["id"])["id"].astype(str)
        columns = self.profiles.columns
        if not set(columns) == set(ids):
            raise ValueError(
                f"Resource profiles column names do not match resource identifiers"
            )
        df = self.profiles.read(columns=columns[0])
        if len(df) not in [8760, 8784]:
            raise ValueError(f"Resource profiles are not either 8760 or 8784 elements")

    def get_clusters(
        self,
        ipm_regions: Iterable[str] = None,
        min_capacity: float = None,
        max_clusters: int = None,
        max_lcoe: float = None,
        cap_multiplier: float = None,
    ) -> pd.DataFrame:
        """
        Compute resource clusters.

        Parameters
        ----------
        ipm_regions
            IPM regions in which to select resources.
            If `None`, all IPM regions are selected.
        min_capacity
            Minimum total capacity (MW). Resources are selected,
            from lowest to highest levelized cost of energy (lcoe),
            or from highest to lowest capacity if lcoe not available,
            until the minimum capacity is just exceeded.
            If `None`, all resources are selected for clustering.
        max_clusters
            Maximum number of resource clusters to compute.
            If `None`, no clustering is performed; resources are returned unchanged.
        max_lcoe
            Select only the resources with a levelized cost of electricity (lcoe)
            below this maximum. Takes precedence over `min_capacity`.
        cap_multiplier
            Multiplier applied to resource capacity before selection by `min_capacity`.

        Returns
        -------
        pd.DataFrame
            Clustered resources whose indices are tuples of the resource identifiers
            from which they were constructed.

        Raises
        ------
        ValueError
            No resources found or selected.
        """
        df = self.metadata.read().set_index("id")
        if ipm_regions is not None:
            # Filter by IPM region
            df = df[df["ipm_region"].isin(ipm_regions)]
        if cap_multiplier is not None:
            # Apply capacity multiplier
            df[CAPACITY] *= cap_multiplier
        # Sort resources by lcoe (ascending) or capacity (descending)
        by = "lcoe" if "lcoe" in df else CAPACITY
        df = df.sort_values(by, ascending=by == "lcoe")
        # Select base resources
        tree = self.group["tree"]
        if tree:
            max_level = df[tree].map(df.groupby(tree)["level"].max())
            base = (df["level"] == max_level).values
            mask = base.copy()
        else:
            mask = np.ones(len(df), dtype=bool)
        if min_capacity:
            # Select resources until min_capacity reached
            temp = (df.loc[mask, CAPACITY].cumsum() < min_capacity).values
            temp[temp.argmin()] = True
            mask[mask] = temp
        if max_lcoe and "lcoe" in df:
            # Selet clusters with LCOE above the cutoff
            mask[mask] = df.loc[mask, "lcoe"] <= max_lcoe
        if not mask.any():
            raise ValueError(f"No resources found or selected")
        # Warn if total capacity less than expected
        capacity = df.loc[mask, CAPACITY].sum()
        if min_capacity and capacity < min_capacity:
            logger.warning(
                f"Selected capacity less than minimum ({capacity} < {min_capacity} MW)"
            )
        # Prepare row merge arguments
        merge = {
            "sums": [key for key in SUMS if key in df] or None,
            "means": [key for key in MEANS if key in df] or None,
            "weight": WEIGHT,
            "uniques": [key for key in UNIQUES if key in df] or None,
        }
        # Compute clusters
        if tree:
            return cluster_row_trees(
                df[mask | ~base], by=by, tree=tree, max_rows=max_clusters, **merge
            )
        return cluster_rows(df[mask], by=df[[by]], max_rows=max_clusters, **merge)

    def get_cluster_profiles(self, ids: Iterable[Iterable]) -> np.ndarray:
        """
        Compute resource cluster profiles.

        Parameters
        ----------
        ids
            Identifiers of the resources to combine in each cluster.

        Returns
        -------
        np.ndarray
            Hourly normalized (0-1) generation profiles (n clusters, m hours).
        """
        # Cast resource identifiers to string to match profile columns
        ids = [[str(x) for x in cids] for cids in ids]
        metadata = self.metadata.read(columns=["id", WEIGHT]).set_index("id")
        metadata.index = metadata.index.astype(str)
        # Compute unique resource identifiers
        columns = []
        for cids in ids:
            columns.extend(cids)
        columns = list(set(columns))
        # Read resource profiles
        profiles = self.profiles.read(columns=columns)
        # Compute cluster profiles
        results = np.zeros((len(ids), len(profiles)), dtype=float)
        for i, cids in enumerate(ids):
            if len(cids) == 1:
                results[i] = profiles[cids[0]].values
            else:
                weights = metadata.loc[cids, WEIGHT].values.astype(float, copy=False)
                weights /= weights.sum()
                results[i] = (profiles[cids].values * weights).sum(axis=1)
        return results


class ClusterBuilder:
    """
    Builds clusters of resources.

    Parameters
    ----------
    groups
        Groups of resources. See :class:`ResourceGroup`.

    Attributes
    ----------
    groups : Iterable[ResourceGroup]
    clusters : List[dict]
        Resource clusters.

        - `group` (ResourceGroup): Resource group from :attr:`groups`.
        - `kwargs` (dict): Parameters used to uniquely identify the group.
        - `region` (str): Model region label.
        - `clusters` (pd.DataFrame): Computed resource clusters.
        - `profiles` (np.ndarray): Computed profiles for the resource clusters.

    Examples
    --------
    Prepare the resource groups.

    >>> groups = []
    >>> group = {'technology': 'utilitypv'}
    >>> metadata = pd.DataFrame({'id': [0, 1], 'ipm_region': ['A', 'A'], 'mw': [1, 2]})
    >>> profiles = pd.DataFrame({'0': np.full(8784, 0.1), '1': np.full(8784, 0.4)})
    >>> groups.append(ResourceGroup(group, metadata, profiles))
    >>> group = {'technology': 'utilitypv', 'existing': True}
    >>> metadata = pd.DataFrame({'id': [0, 1], 'ipm_region': ['B', 'B'], 'mw': [1, 2]})
    >>> profiles = pd.DataFrame({'0': np.full(8784, 0.1), '1': np.full(8784, 0.4)})
    >>> groups.append(ResourceGroup(group, metadata, profiles))
    >>> builder = ClusterBuilder(groups)

    Incrementally build clusters and export the results.

    >>> builder.build_clusters(region='A', ipm_regions=['A'], max_clusters=1,
    ...     technology='utilitypv', existing=False)
    >>> builder.build_clusters(region='B', ipm_regions=['B'], min_capacity=2,
    ...     technology='utilitypv', existing=True)
    >>> builder.get_cluster_metadata()
          ids ipm_region  mw region technology  existing
    0  (1, 0)          A   3      A  utilitypv     False
    1    (1,)          B   2      B  utilitypv      True
    >>> builder.get_cluster_profiles()
    array([[0.3, 0.3, 0.3, ..., 0.3, 0.3, 0.3],
           [0.4, 0.4, 0.4, ..., 0.4, 0.4, 0.4]])

    Errors arise if search criteria is either ambiguous or results in an empty result.

    >>> builder.build_clusters(region='A', ipm_regions=['A'], technology='utilitypv')
    Traceback (most recent call last):
      ...
    ValueError: Parameters match multiple resource groups: [{...}, {...}]
    >>> builder.build_clusters(region='A', ipm_regions=['B'],
    ...     technology='utilitypv', existing=False)
    Traceback (most recent call last):
      ...
    ValueError: No resources found or selected
    """

    def __init__(self, groups: Iterable[ResourceGroup]) -> None:
        self.groups = groups
        self.clusters: List[dict] = []

    @classmethod
    def from_path(cls, path: Union[str, os.PathLike] = ".") -> "ClusterBuilder":
        """
        Load resources from directory.

        Reads all files matching pattern '*_group.json'.

        Parameters
        ----------
        path
            Path to directory.

        Raises
        ------
        FileNotFoundError
            No resource groups found in path.
        """
        paths = glob.glob(os.path.join(path, "*_group.json"))
        if not paths:
            raise FileNotFoundError(f"No resource groups found in {path}")
        return cls([ResourceGroup.from_json(p) for p in paths])

    def _test_clusters_exist(self) -> None:
        if not self.clusters:
            raise ValueError("No clusters have been built")

    def find_groups(self, **kwargs: Any) -> List[ResourceGroup]:
        """
        Return the resource groups matching the specified arguments.

        Parameters
        ----------
        **kwargs
            Parameters to match against resource group metadata.
        """
        return [
            rg
            for rg in self.groups
            if all(k in rg.group and rg.group[k] == v for k, v in kwargs.items())
        ]

    def build_clusters(
        self,
        region: str,
        ipm_regions: Iterable[str] = None,
        min_capacity: float = None,
        max_clusters: int = None,
        max_lcoe: float = None,
        cap_multiplier: float = None,
        **kwargs: Any,
    ) -> None:
        """
        Build and append resource clusters to the collection.

        This method can be called as many times as desired before generating outputs.
        See :meth:`ResourceGroup.get_clusters` for parameter descriptions.

        Parameters
        ----------
        region
            Model region (used only to label results).
        ipm_regions
        min_capacity
        max_clusters
        max_lcoe
        cap_multiplier
        **kwargs
            Parameters to :meth:`find_groups` for selecting the resource group.

        Raises
        ------
        ValueError
            Parameters match multiple resource groups.
        """
        groups = self.find_groups(**kwargs)
        if len(groups) > 1:
            meta = [rg.group for rg in groups]
            raise ValueError(f"Parameters match multiple resource groups: {meta}")
        c = {
            "group": groups[0],
            "kwargs": kwargs,
            "region": region,
            "clusters": groups[0].get_clusters(
                ipm_regions=ipm_regions,
                min_capacity=min_capacity,
                max_clusters=max_clusters,
                max_lcoe=max_lcoe,
                cap_multiplier=cap_multiplier,
            ),
        }
        c["profiles"] = groups[0].get_cluster_profiles(ids=c["clusters"].index)
        self.clusters.append(c)

    def get_cluster_metadata(self) -> pd.DataFrame:
        """
        Return computed cluster metadata.

        The following fields are added:

        - `ids` (tuple): Original resource identifiers
        - `region` (str): Region label passed to :meth:`build_clusters`.
        - **kwargs: Parameters used to uniquely identify the group.

        Raises
        ------
        ValueError
            No clusters have yet been computed.
        """
        self._test_clusters_exist()
        dfs = []
        for c in self.clusters:
            df = c["clusters"]
            df = (
                df.assign(region=c["region"], **c["kwargs"])
                .rename_axis("ids")
                .reset_index()
            )
            dfs.append(df)
        return pd.concat(dfs, axis=0, ignore_index=True, sort=False)

    def get_cluster_profiles(self) -> np.ndarray:
        """
        Return computed cluster profiles.

        Returns
        -------
        np.ndarray
            Hourly normalized (0-1) generation profiles (n clusters, m hours).

        Raises
        ------
        ValueError
            No clusters have yet been computed.
        """
        self._test_clusters_exist()
        return np.row_stack([c["profiles"] for c in self.clusters])


def _tuple(x: Any) -> tuple:
    """
    Cast object to tuple.

    Examples
    --------
    >>> _tuple(1)
    (1,)
    >>> _tuple([1])
    (1,)
    >>> _tuple('string')
    ('string',)
    """
    if np.iterable(x) and not isinstance(x, str):
        return tuple(x)
    return (x,)


def _unique(x: Iterable) -> Any:
    """
    Return the unique value (if it is unique).

    Examples
    --------
    >>> _unique((1, 2)) is None
    True
    >>> _unique((1, 1))
    1
    >>> _unique(['a', 'b']) is None
    True
    >>> _unique(['a', 'a'])
    'a'
    """
    unique = pd.Series(x).unique()
    if len(unique) == 1:
        return unique[0]
    return None


def merge_row_pair(
    a: Mapping,
    b: Mapping,
    sums: Iterable = None,
    means: Iterable = None,
    weight: Any = None,
    uniques: Iterable = None,
) -> dict:
    """
    Merge two mappings into one.

    Parameters
    ----------
    a
        First mapping (e.g. :class:`dict`, :class:`pd.Series`).
    b
        Second mapping.
    means
        Keys of values to average.
    weight
        Key of values to use as weights for weighted averages.
        If `None`, averages are not weighted.
    uniques
        Keys of values for which to return the value if equal, and `None` if not.

    Returns
    -------
    dict
        Merged row as a dictionary.

    Examples
    --------
    >>> df = pd.DataFrame({'mw': [1, 2], 'area': [10, 20], 'lcoe': [0.1, 0.4]})
    >>> a, b = df.to_dict('rows')
    >>> merge_row_pair(a, b, sums=['area', 'mw'], means=['lcoe'], weight='mw')
    {'area': 30, 'mw': 3, 'lcoe': 0.3}
    >>> merge_row_pair(a, b, sums=['area', 'mw'], means=['lcoe'])
    {'area': 30, 'mw': 3, 'lcoe': 0.25}
    >>> b['mw'] = 1
    >>> merge_row_pair(a, b, uniques=['mw', 'area'])
    {'mw': 1, 'area': None}
    """
    merge = {}
    if sums:
        for key in sums:
            merge[key] = a[key] + b[key]
    if means:
        if weight:
            total = a[weight] + b[weight]
            aw = a[weight] / total
            bw = b[weight] / total
        else:
            aw = 0.5
            bw = 0.5
        for key in means:
            merge[key] = a[key] * aw + b[key] * bw
    if uniques:
        for key in uniques:
            merge[key] = a[key] if a[key] == b[key] else None
    return merge


def cluster_rows(
    df: pd.DataFrame, by: Iterable[Iterable], max_rows: int = None, **kwargs: Any
) -> pd.DataFrame:
    """
    Merge rows in dataframe by hierarchical clustering.

    Uses the Ward variance minimization algorithm to incrementally merge rows.
    See :func:`scipy.cluster.hierarchy.linkage`.

    Parameters
    ----------
    df
        Rows to merge (m, ...).
    by
        2-dimensional array of observation vectors (m, ...) from which to compute
        distances between each row pair.
    max_rows
        Number of rows at which to stop merging rows.
        If `None`, no clustering is performed.
    **kwargs
        Optional parameters to :func:`merge_row_pair`.

    Returns
    -------
    pd.DataFrame
        Merged rows as a dataframe.
        Their indices are tuples of the original row indices from which they were built.
        If original indices were already iterables, they are merged
        (e.g. (1, 2) and (3, ) becomes (1, 2, 3)).

    Raises
    ------
    ValueError
        Max number of rows must be greater than zero.

    Examples
    --------
    With the default (range) row index:

    >>> df = pd.DataFrame({'mw': [1, 2, 3], 'area': [4, 5, 6], 'lcoe': [0.1, 0.4, 0.2]})
    >>> kwargs = {'sums': ['area', 'mw'], 'means': ['lcoe'], 'weight': 'mw'}
    >>> cluster_rows(df, by=df[['lcoe']], **kwargs)
          mw  area  lcoe
    (0,)   1     4   0.1
    (1,)   2     5   0.4
    (2,)   3     6   0.2
    >>> cluster_rows(df, by=df[['lcoe']], max_rows=2, **kwargs)
            mw  area   lcoe
    (1,)     2     5  0.400
    (0, 2)   4    10  0.175

    With a custom row index:

    >>> df.index = ['a', 'b', 'c']
    >>> cluster_rows(df, by=df[['lcoe']], max_rows=2, **kwargs)
            mw  area   lcoe
    (b,)     2     5  0.400
    (a, c)   4    10  0.175

    With an iterable row index:

    >>> df.index = [(1, 2), (4, ), (3, )]
    >>> cluster_rows(df, by=df[['lcoe']], max_rows=2, **kwargs)
               mw  area   lcoe
    (4,)        2     5  0.400
    (1, 2, 3)   4    10  0.175
    """
    nrows = len(df)
    if max_rows is None:
        max_rows = len(df)
    elif max_rows < 1:
        raise ValueError("Max number of rows must be greater than zero")
    drows = nrows - max_rows
    index = [_tuple(x) for x in df.index] + [None] * drows
    df = df.reset_index(drop=True)
    if drows < 1:
        df.index = index
        return df
    # Convert dataframe rows to dictionaries
    rows = df.to_dict("rows")
    # Preallocate new rows
    rows += [None] * drows
    # Preallocate new rows
    Z = scipy.cluster.hierarchy.ward(by)
    n = nrows + drows
    mask = np.ones(n, dtype=bool)
    for i, link in enumerate(Z[:drows, 0:2].astype(int)):
        mask[link] = False
        pid = nrows + i
        rows[pid] = merge_row_pair(rows[link[0]], rows[link[1]], **kwargs)
        index[pid] = index[link[0]] + index[link[1]]
    clusters = pd.DataFrame([x for x, m in zip(rows, mask) if m])
    # Preserve original column order
    clusters = clusters[[x for x in df.columns if x in clusters]]
    clusters.index = [x for x, m in zip(index, mask) if m]
    return clusters


def build_row_tree(
    df: pd.DataFrame, by: Iterable[Iterable], max_level: int = None, **kwargs: Any,
) -> pd.DataFrame:
    """
    Build a hierarchical tree of rows in a dataframe.

    Uses the Ward variance minimization algorithm to incrementally merge rows.
    See :func:`scipy.cluster.hierarchy.linkage`.

    Parameters
    ----------
    df
        Rows to merge (m, ...).
        Should not have columns `id`, `parent_id`, and `level`, as these are appended to
        the result dataframe.
    by
        2-dimensional array of observation vectors (m, ...) from which to compute
        distances between each row pair.
    max_level
        Maximum level of tree to return,
        from m (the number of rows in `df`, if `None`) to 1.
    **kwargs
        Optional parameters to :func:`merge_row_pair`.

    Returns
    -------
    pd.DataFrame
        Hierarchical tree as a dataframe.
        Row indices are tuples of the original row indices from which they were built.
        If original indices were already iterables, they are merged
        (e.g. (1, 2) and (3, ) becomes (1, 2, 3)).
        The following columns are added:

        - `id` (int): New row identifier (0, ..., 0 + n).
        - `parent_id` (Int64): New row identifer of parent row.
        - `level` (int): Tree level of row (max_level, ..., 1).

    Raises
    ------
    ValueError
        Max level of tree must be greater than zero.

    Examples
    --------
    >>> df = pd.DataFrame({'mw': [1, 2, 3], 'area': [4, 5, 6], 'lcoe': [0.1, 0.4, 0.2]})
    >>> kwargs = {'sums': ['area', 'mw'], 'means': ['lcoe'], 'weight': 'mw'}
    >>> build_row_tree(df, by=df[['lcoe']], **kwargs)
               mw  area   lcoe  id  parent_id  level
    (0,)        1     4  0.100   0          3      3
    (1,)        2     5  0.400   1          4      3
    (2,)        3     6  0.200   2          3      3
    (0, 2)      4    10  0.175   3          4      2
    (1, 0, 2)   6    15  0.250   4        NaN      1
    >>> build_row_tree(df, by=df[['lcoe']], max_level=2, **kwargs)
               mw  area   lcoe  id  parent_id  level
    (1,)        2     5  0.400   0          2      2
    (0, 2)      4    10  0.175   1          2      2
    (1, 0, 2)   6    15  0.250   2        NaN      1
    >>> build_row_tree(df, by=df[['lcoe']], max_level=1, **kwargs)
               mw  area  lcoe  id  parent_id  level
    (1, 0, 2)   6    15  0.25   0        NaN      1
    """
    nrows = len(df)
    if max_level is None:
        max_level = nrows
    else:
        max_level = min(max_level, nrows)
        if max_level < 1:
            raise ValueError("Max level of tree must be greater than zero")
    drows = nrows - 1
    index = [_tuple(x) for x in df.index] + [None] * drows
    df = df.reset_index(drop=True)
    if drows < 1:
        df.index = index
        return df
    # Convert dataframe rows to dictionaries
    rows = df.to_dict("rows")
    # Preallocate new rows
    rows += [None] * drows
    Z = scipy.cluster.hierarchy.linkage(by, method="ward")
    n = nrows + drows
    mask = np.ones(n, dtype=bool)
    level = np.concatenate((np.full(nrows, nrows), np.arange(drows, 0, -1)))
    parent_id = np.zeros(n)
    drop = nrows - max_level
    for i, link in enumerate(Z[:, 0:2].astype(int)):
        if i < drop:
            mask[link] = False
        pid = nrows + i
        parent_id[link] = pid
        rows[pid] = merge_row_pair(rows[link[0]], rows[link[1]], **kwargs)
        index[pid] = index[link[0]] + index[link[1]]
    tree = pd.DataFrame([x for x, m in zip(rows, mask) if m])
    # Preserve original column order
    tree = tree[[x for x in df.columns if x in tree]]
    # Normalize ids to 0, ..., n
    old_ids = np.where(mask)[0]
    new_ids = np.arange(len(old_ids))
    new_parent_ids = pd.Series(np.searchsorted(old_ids, parent_id[mask]), dtype="Int64")
    new_parent_ids.iloc[-1] = np.nan
    # Bump lower levels to max_level
    level = level[mask]
    if max_level < nrows:
        stop = level.size - np.searchsorted(level[::-1], max_level, side="right")
        level[:stop] = max_level
    tree = tree.assign(id=new_ids, parent_id=new_parent_ids, level=level)
    tree.index = [x for x, m in zip(index, mask) if m]
    return tree


def cluster_row_trees(
    df: pd.DataFrame, by: str, tree: str = None, max_rows: int = None, **kwargs: Any
) -> pd.DataFrame:
    """
    Merge rows in a dataframe following precomputed hierarchical trees.

    Parameters
    ----------
    df
        Rows to merge.
        Must have columns `parent_id` (matching values in index), `level`, and
        the columns named in **by** and **tree**.
    by
        Name of column to use for determining merge order.
        Children with the smallest pairwise distance on this column are merged first.
    tree
        Name of column to use for differentiating between hierarchical trees.
        If `None`, assumes rows represent a single tree.
    max_rows
        Number of rows at which to stop merging rows.
        If smaller than the number of trees, :func:`cluster_rows` is used to merge
        tree heads.
        If `None`, no merging is performed and only the base rows are returned.
    **kwargs
        Optional parameters to :func:`merge_row_pair`.

    Returns
    -------
    pd.DataFrame
        Merged rows as a dataframe.
        Their indices are tuples of the original row indices from which they were built.
        If original indices were already iterables, they are merged
        (e.g. (1, 2) and (3, ) becomes (1, 2, 3)).

    Raises
    ------
    ValueError
        Max number of rows must be greater than zero.
    ValueError
        Missing required fields.
    ValueError
        `by` column not included in row merge arguments (`kwargs`).

    Examples
    --------
    >>> df = pd.DataFrame({
    ...     'level': [3, 3, 3, 2, 1],
    ...     'parent_id': pd.Series([3, 3, 4, 4, float('nan')], dtype='Int64'),
    ...     'mw': [0.1, 0.1, 0.1, 0.2, 0.3]
    ... }, index=[0, 1, 2, 3, 4])
    >>> cluster_row_trees(df, by='mw', sums=['mw'], max_rows=2)
             mw
    (2,)    0.1
    (0, 1)  0.2
    >>> cluster_row_trees(df, by='mw', sums=['mw'], max_rows=1)
                mw
    (2, 0, 1)  0.3
    >>> cluster_row_trees(df, by='mw', sums=['mw'])
           mw
    (0,)  0.1
    (1,)  0.1
    (2,)  0.1
    """
    required = ["parent_id", "level", by]
    if tree:
        required.append(tree)
    missing = [key for key in required if key not in df]
    if missing:
        raise ValueError(f"Missing required fields {missing}")
    if tree:
        mask = df["level"] == df[tree].map(df.groupby(tree)["level"].max())
    else:
        mask = df["level"] == df["level"].max()
    nrows = mask.sum()
    if max_rows is None:
        max_rows = nrows
    elif max_rows < 1:
        raise ValueError("Max number of rows must be greater than zero")
    columns = (
        (kwargs.get("sums") or [])
        + (kwargs.get("means") or [])
        + (kwargs.get("uniques") or [])
    )
    if by not in columns:
        raise ValueError(f"{by} not included in row merge arguments")
    drows = nrows - max_rows
    if drows < 1:
        df = df.copy().loc[mask, columns]
        df.index = [_tuple(x) for x in df.index]
        return df
    df = df.assign(_id=df.index, _ids=[_tuple(x) for x in df.index], _mask=mask)
    diff = lambda x: abs(x.max() - x.min())
    while drows > 0:
        # Sort parents by ascending distance of children
        # NOTE: Inefficient to recompute for all parents every time
        parents = (
            df[df["_mask"]]
            .groupby("parent_id", sort=False)
            .agg(ids=("_id", list), n=("_id", "count"), distance=(by, diff))
            .sort_values(["n", "distance"], ascending=[False, True])
        )
        if parents.empty:
            break
        if parents["n"].iloc[0] == 2:
            # Choose complete parent with lowest distance of children
            pid = parents.index[0]
            ids = parents["ids"].iloc[0]
            # Compute parent
            parent = {
                # Initial attributes
                **df.loc[[pid], ["_id", "parent_id", "level"]].to_dict("rows")[0],
                # Merged children attributes
                # NOTE: Needed only if a child is incomplete
                **merge_row_pair(df.loc[ids[0]], df.loc[ids[1]], **kwargs),
                # Indices of all past children
                "_ids": [df.loc[ids[0], "_ids"] + df.loc[ids[1], "_ids"]],
                "_mask": True,
            }
            # Add parent
            df.loc[[pid]] = pd.DataFrame(parent, index=[pid])
            # Drop children
            df.loc[ids, "_mask"] = False
            # Decrement rows
            drows -= 1
        else:
            # Promote child with deepest parent
            parent_id = df.loc[parents.index, "level"].idxmax()
            child_id = parents.loc[parent_id, "ids"][0]
            # Update child
            columns = ["_id", "parent_id", "level"]
            df.loc[child_id, columns] = df.loc[parent_id, columns]
            # Update index
            df.rename(index={child_id: parent_id, parent_id: np.nan}, inplace=True)
    # Apply mask
    df = df[df["_mask"]]
    # Drop temporary columns
    df.index = df["_ids"].values
    df = df.drop(columns=["_id", "_ids", "_mask"])
    if len(df) > max_rows:
        df = cluster_rows(df, by=df[[by]], max_rows=max_rows, **kwargs)
    return df[columns]


def group_rows(
    df: pd.DataFrame, ids: Iterable[Iterable]
) -> pd.core.groupby.DataFrameGroupBy:
    """
    Group dataframe rows by index.

    Parameters
    ----------
    df
        Dataframe to group.
    ids
        Groups of rows indices.

    Returns
    -------
    pd.core.groupby.DataFrameGroupBy
        Rows of `df` grouped by their membership in each index group.

    Examples
    --------
    >>> df = pd.DataFrame({'x': [2, 1, 3]}, index=[2, 1, 3])
    >>> group_rows(df, [(1, ), (2, 3), (1, 2, 3)]).sum()
       x
    0  1
    1  5
    2  6
    """
    groups = np.repeat(np.arange(len(ids)), [len(x) for x in ids])
    index = np.concatenate(ids)
    return df.loc[index].groupby(groups, sort=False)
