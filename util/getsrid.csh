#/bin/csh

# get srid of a shapefile
#
# usage:
# source gegsrid.csh shapefile.shp
#
# A csh variable `mysrid` to the srid for the shape file
#
# csh variable `dbname` `user` `server` `schema` are needed, similar to load.shapefile.2017.csh
#
# this script imports one feature from shapefile into postgis database, and
# then read the srid from the feature

#
# there sould be alternative ways, like below.  But None of them worked
# reliably for the set of shp file we have
# 
# ogrinfo -e shapefile.shp
#
# gdalsrsinfo -e shapefle.shp
# 
# OGRSpatialReference::AutoIdentfyEPSG()
# https://gis.stackexchange.com/questions/372381/is-there-an-alternative-to-prj2epsg-org
#
# OGRSpatialReference::GetAuthorityCode()
# https://gis.stackexchange.com/questions/20298/is-it-possible-to-get-the-epsg-value-from-an-osr-spatialreference-class-using-th


set _shpfile="$1"
set _tbl = `basename ${_shpfile} | sed 's/.shp$//'`
ogr2ogr -f "PostgreSQL" "PG:dbname=$dbname user=$user host=$server" \
  ${_shpfile} \
  -sql 'SELECT * FROM '${_tbl}' LIMIT 1' -lco GEOMETRY_NAME=geom \
  -lco PRECISION=NO -nlt PROMOTE_TO_MULTI -nln \
  $schema.scratch -overwrite

set mysrid=`psql -h $server -U $user -q $dbname --tuples-only -c "SELECT ST_Srid(geom) from $schema.scratch;"`

# clean up
psql -h $server -U $user -q $dbname -c "DROP TABLE $schema.scratch;"
unset _shpfile
unset _tbl

