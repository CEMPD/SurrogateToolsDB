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

set indir=$shpdir/US
set shapefile=tl2019Counties_LCC_WRF
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPolygon
source load_shapefile.2017.csh

### Load population and housing shapefile, and calculate density
set indir=$shpdir/Census
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

### Load public schools shapefile for surrogate 508
set indir=$shpdir/NCES
set shapefile=public_schools_2018_2019
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set geomtype=MultiPoint
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
set indir=$shpdir/OilGas_2016
set shapefile=ASSOCIATED_GAS_PRODUCTION_CONUS_2016 # 672
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=CBM_PRODUCTION_CONUS_2016 # 673, 699
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=CBM_WELLS_CONUS_2016 # 691
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=COMPLETIONS_ALL_CONUS_2016 # 686
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=COMPLETIONS_CBM_CONUS_2016 # 679
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=COMPLETIONS_GAS_CONUS_2016 # 678
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=COMPLETIONS_OIL_CONUS_2016 # 685
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=COMPLETIONS_UNCONVENTIONAL_CONUS_2016 # 674
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=CONDENSATE_CBM_PRODUCTION_CONUS_2016 # 673
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=CONDENSATE_GAS_PRODUCTION_CONUS_2016 # 697
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=FEET_DRILLED_CONUS_2016 # 687
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=GAS_PRODUCTION_CONUS_2016 # 672, 689, 696, 697
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=GAS_WELLS_CONUS_2016 # 698
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=OIL_PRODUCTION_CONUS_2016 # 684
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=OIL_WELLS_CONUS_2016 # 695
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=PRODUCED_WATER_ALL_CONUS_2016 # 683
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=PRODUCED_WATER_CBM_CONUS_2016 # 6831
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=PRODUCED_WATER_GAS_CONUS_2016 # 6832
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=PRODUCED_WATER_OIL_CONUS_2016 # 6833
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=SPUD_ALL_CONUS_2016 # 692
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=SPUD_CBM_CONUS_2016 # 670
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=SPUD_GAS_CONUS_2016 # 671
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=SPUD_OIL_CONUS_2016 # 681
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=TOTAL_EXPL_WELL_CONUS_2016 # 677
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=TOTAL_GAS_PRODUCTION_CONUS_2016 # 689
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=TOTAL_PROD_WELL_CONUS_2016 # 676
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
source load_shapefile.2017.csh

set shapefile=TOTAL_WELL_CONUS_2016 # 693
set table=`echo $shapefile | tr "[:upper:]" "[:lower:]"`
set attr="ACTIVITY"
set geomtype=MultiPolygon
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
