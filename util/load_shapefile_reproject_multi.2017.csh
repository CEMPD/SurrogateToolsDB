#!/bin/csh -f

# Load the original data shapefile (shp) with its projection (prj)
# Tranform to a new projection (from original 900922 to new 900921) and create gist index on it
# Check whether the shapefile data are imported correclty or not.

source ../pg_setup.csh
set user=$PG_USER
set server=$DBSERVER
set dbname=$DBNAME
set schema=public
set srid=900921
set newfield=geom_${srid}          
set org_geom_field=wkb_geometry

set shpdir=$SRG_HOME/data/emiss_shp2017
### Load county shapefile
set indir=$shpdir/Census
set shapefile=cb_2017_us_county_500k
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr=""
set geomtype=MultiPolygon          # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

### Load population and housing shapefile, and calculate density
set shapefile=acs2016_5yr_bg
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon          # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

### Load FEMA
set indir=$shpdir/FEMA
set shapefile=fema_bsf_2002bnd
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr=""
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

### Load hpms shapefile, transfer column move2014 to integer
set indir=$shpdir/HPMS
set shapefile=hpms2017_v3_04052020
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr=""
set geomtype=MultiLineString     # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

### Load pil shapefile for surrogate 205, Potential Idling Locations
set indir=$shpdir/PIL
#set shapefile=pil_2018_08_17
set shapefile=pil_2019_06_24
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPoint       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

###  Load Bus Terminals shapefile for surrogates 258 and 259
set indir=$shpdir/NTAD
set shapefile=NTAD_2016_ipcd
#set shapefile=Intermodal_Passenger_Connectivity_Database_IPCD
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPoint       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

###  Load Waterway shapefile for surrogates 807
#set shapefile=NTAD_2014_Waterway
set shapefile=NTAD_d2019_Waterway
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiLineString       # retrieve the exact geopmetry type from the table.
set attr="length"
source load_shapefile.2017.csh

# for 350,807,820 data shapefile: NTAD_2014_County_Pol ?
set indir=$shpdir/NTAD
set shapefile=NTAD_2017_County_Pol
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr=""
set geomtype=MultiPolygon          # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

###  Load Railroad shapefile for surrogates 261, 271-273
set shapefile=NTAD_2014_Rail
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiLineString       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

### Load NLCD shapefile ? or nlcd2011_500mv2*
set indir=$shpdir/NLCD
set shapefile=CONUS_AK_NLCD_2011_500m_WGS
#set shapefile=nlcd_2016
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr=""
set geomtype=MultiPolygon        # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

###  Load Waterway shapefile for surrogates 807
set indir=$shpdir/TIGER
set shapefile=TIGER_2014_Rail #under emiss_shp2014
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiLineString       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

# Losd  Refineries and Tank Farms
set indir=$shpdir/EIA
set shapefile=EIA_2015_US_Oil
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr=""
set geomtype=MultiPoint       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

set indir=$shpdir/USGS
set shapefile=USGS_2011_mines
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPoint 
source load_shapefile.2017.csh

set indir=$shpdir/POI
set shapefile=usa_golf_courses_2019_10
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPoint 
source load_shapefile.2017.csh

set indir=$shpdir/US
set shapefile=airport_area
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="area"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

# 808 2013 Shipping Density
set indir=$shpdir/CMV    
set shapefile=CMV_2013_Vessel_Density_CONUS1km
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="mean"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

### Load Oil Gas shapefile
set indir=$shpdir/OilGas
set shapefile=AllExploratoryWells # 677 TOTAL_EX_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="TOTAL_EX_1"
set geomtype=MultiPolygon    # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

set shapefile=AllWells   # 693 TOTAL_WE_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon    # retrieve the exact geopmetry type from the table.
set attr="TOTAL_WE_1"
source load_shapefile.2017.csh

set shapefile=CBMProduction  # 699 CBM_PROD_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon    # retrieve the exact geopmetry type from the table.
set attr="CBM_PROD_1"
source load_shapefile.2017.csh

set shapefile=CBMWell_Counts  # 691 CBM_WELLS1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon    # retrieve the exact geopmetry type from the table.
set attr="CBM_WELLS1"
source load_shapefile.2017.csh

set shapefile=Completions_CBM  # 679 COMPLETI_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon         # retrieve the exact geopmetry type from the table.
set attr="COMPLETI_1"
source load_shapefile.2017.csh

set shapefile=Completions_Oil  # 685 COMPLETI_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon         # retrieve the exact geopmetry type from the table.
set attr="COMPLETI_1"
source load_shapefile.2017.csh

set shapefile=FeetDrilled_All  # 687 FEET_DRI_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon         # retrieve the exact geopmetry type from the table.
set attr="FEET_DRI_1"
source load_shapefile.2017.csh

set shapefile=GasProduction  # 696 GAS_PROD_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon         # retrieve the exact geopmetry type from the table.
set attr="GAS_PROD_1"
source load_shapefile.2017.csh

set shapefile=Spud_Count_All_20161111  # 692 SPUD_COU_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon         # retrieve the exact geopmetry type from the table.
set attr="SPUD_COU_1"
source load_shapefile.2017.csh

set shapefile=GasWell_Counts  # 698 GAS_WELL_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
set attr="GAS_WELL_1"
source load_shapefile.2017.csh

set shapefile=OilProduction   # 694 OIL_PROD_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="OIL_PROD_1"
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

set shapefile=OilWell_Counts_20161111  # 695 OIL_WELL_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="OIL_WELL_1"
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

set shapefile=ProducedWater_All  # 683 PRODUCED_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="PRODUCED_1"
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
source load_shapefile.2017.csh

set shapefile=SpudCount_CBM  # 670, SPUD_COU_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
set attr="SPUD_COU_1"
source load_shapefile.2017.csh

set shapefile=SpudCount_Gas  #671 SPUD_COU_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
set attr="SPUD_COU_1"
source load_shapefile.2017.csh

set shapefile=SpudCount_HF  # 674 SPUD_HF_21
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
set attr="SPUD_HF_21"
source load_shapefile.2017.csh

set shapefile=SpudCount_Oil # 681 SPUD_COU_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon       # retrieve the exact geopmetry type from the table.
set attr="SPUD_COU_1"
source load_shapefile.2017.csh

set shapefile=Completions_Gas  # 678 COMPLETI_1
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon          # retrieve the exact geopmetry type from the table.
set attr="COMPLETI_1"
source load_shapefile.2017.csh

### Load Shipping shapefile for 805, 806
set indir=$shpdir/EPA
set shapefile=ShippingLanes_2014NEI
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr=""
set geomtype=MultiPolygon
source load_shapefile.2017.csh

# Ports, has ring problem? can be ignored?
set shapefile=Ports_2014NEI
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon          # retrieve the exact geopmetry type from the table.
set attr=""
source load_shapefile.2017.csh
