# PowerGenome

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)
[![Build Status](https://travis-ci.com/gschivley/PowerGenome.svg?token=yTGQ4JcCGLW2GZpmvXHw&branch=master)](https://travis-ci.com/gschivley/PowerGenome)
[![codecov](https://codecov.io/gh/gschivley/PowerGenome/branch/master/graph/badge.svg?token=7KJYLE3jOW)](https://codecov.io/gh/gschivley/PowerGenome)

Power system optimization models can be used to explore the cost and emission implications of different regulations in future energy systems. One of the most difficult parts of running these models is assembling all the data. A typical model will define several regions, each of which need data such as:

- All existing generating units (perhaps grouped into a few discrete clusters within each region)
- Transmission constraints between regions
- Hourly load profiles (including new loads from vehicle and building electrification)
- Hourly generation profiles for wind & solar
- Cost estimates for new generating units

Because computational complexity and run times increase as the number of regions and generating unit clusters increases, a user might want only want to disaggregate regions and generating units close to the primary region of interest. For example, a study focused on clean electricity regulations in New Mexico might combine several states in the Pacific Northwest into a single region while also splitting Arizona combined cycle units into multiple clusters.

The goal of PowerGenome is to let a user make all of these choices in a settings file and then run a single script that generates input files for the power system model. PowerGenome currently generates input files for [GenX](https://energy.mit.edu/wp-content/uploads/2017/10/Enhanced-Decision-Support-for-a-Changing-Electricity-Landscape.pdf), and we hope to expand to other models in the near future.

## Data

PowerGenome uses data from a number of different sources, including EIA, NREL, and EPA. Most of the data are already compiled into a [single sqlite database](https://drive.google.com/open?id=18tLKbok1-me81SkfWAhSLXmy5HW6RdvI) (see instructions for using it below). There are also a few data files stored in this repository:

- Regional cost multipliers for individual technologies developed by EIA (`data/cost_multipliers/EIA regional cost multipliers.csv`).
- A simplified geojson version of EPA's shapefile for IPM regions (`data/ipm_regions_simple.geojson`).
- Information on user-defined technologies, which can be included in outputs. This can be used to define a custom cost case (e.g. $500/kW PV) or a new technology such as natural gas with 100% carbon capture. The CSV files are stored in `data/additional_technologies` and there is a documentation file in that folder describing what to include in the file.

There are quite a few data inputs that we have not yet compiled for public use with PowerGenome. These include 2011 weather year wind/solar profiles for both existing and new-build resources by IPM region, electrification demand profiles to modify the 2011 IPM load shapes, and state electricity policies. [Contact us](mailto:powergenome@carbonimpact.co) if you want to help compile data.

## PUDL Dependency

This project pulls data from [PUDL](https://github.com/catalyst-cooperative/pudl). As such, it requires installation of PUDL to access a normalized sqlite database and some of the convienience PUDL functions.

`catalystcoop.pudl` is included in the `environment.yml` file and will be installed automatically in the conda environment (see instructions below). The data used by PowerGenome have outstripped what is available in the public version of PULD, so download a modifed version of the [PUDL sqlite database here](https://drive.google.com/open?id=1LixZ6atMetvPRQlhO0kSWzzmIMh6GGw3). The package `catalystcoop.pudl` must be version 0.3.0 or above to work with this version of the database.

## Installation

1. Clone this repository to your local machine and navigate to the top level (PowerGenome) folder.

2. Create a conda environment named `powergenome` using the provided `environment.yml` file.

```sh
conda env create -f environment.yml
```

3. Activate the `powergenome` environment.

```sh
conda activate powergenome
```

4. pip-install an editable version of this project

```sh
pip install -e .
```

5. Download a [modifed version of the PULD database](https://drive.google.com/open?id=18tLKbok1-me81SkfWAhSLXmy5HW6RdvI) that includes NREL ATB cost data and is not yet included in PUDL.

6. Once you have downloaded the sqlite database, change the `SETTINGS["pudl_db"]` parameter in `powergenome/params.py` to match the path on your computer.

7. Get an [API key for EIA's OpenData portal](https://www.eia.gov/opendata/register.php). This key is needed to download projected fuel prices from the 2019 Annual Energy Outlook. Create the file `PowerGenome/powergenome/.env` and save the key in this file using the format `EIA_API_KEY=YOUR_KEY_HERE`. No quotation marks are needed around the API string. The `.env` file is included in `.gitignore` and will not be synced with the repository.

## Running code

Settings are controlled in a YAML file. An example set of settings files, which sets up a small 2-zone model of California, is included in the folder `example_system`.

The code is currently structured in a series of modules - `load_data.py`, `generators.py`, `transmission.py`, `nrelatb.py`, `eia_opendata.py`, `load_profiles.py`, and a couple others. The code and architecture is under active development. While the outputs are all formatted for GenX we hope to make the data formatting code more module to allow users to easily switch between outputs for different power system models.

Functions from each module can be imported and used in an interactive environment (e.g. JupyterLab). To run from the command line, navigate to a project folder that contains a settings file (e.g. `myproject/powergenome`), activate the  `powergenome` conda environment, and use the command `run_powergenome_multiple` with flags for the settings file name and where the results should be saved:

```sh
run_powergenome_multiple --settings_file test_settings.yml --results_folder test_system
```

If you have previously installed PowerGenome and the `run_powergenome_multiple` command doesn't work, try reinstalling it using `pip install -e .` as described above.

## Contributing

Contributions are welcome! There is significant work to do on this project and additional perspective on user needs will help make it better. If you see something that needs to be improved, [open an issue](https://github.com/gschivley/PowerGenome/issues). If you have questions or need assistance, join [PowerGenome on groups.io](https://groups.io/g/powergenome) and post a message there.

Pull requests are always welcome. To start modifying/adding code, make a fork of this repository, create a new branch, and [submit a pull request](https://help.github.com/en/github/collaborating-with-issues-and-pull-requests/creating-a-pull-request-from-a-fork).

All code added to the project should be formatted with [black](https://black.readthedocs.io/en/stable/). After making a fork and cloning it to your own computer, run `pre-commit install` to [install the git hook scripts](https://pre-commit.com/#3-install-the-git-hook-scripts) that will run every time you make a commit. These hooks will automatically run `black` (in case you forgot), fix trailing whitespace, check yaml formatting, etc.
