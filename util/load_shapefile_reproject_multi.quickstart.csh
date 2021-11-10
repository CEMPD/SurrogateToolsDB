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
