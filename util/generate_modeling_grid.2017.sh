#!/bin/bash
# create_grid_900921.sh modified 8/3/2016 from below
# create_grid_900915.sh modified 8/30/2016 to change projection from 900915 to 900921
# create_grid.sh original by Catherine Seppanen
# created 6/9/2016 Jo Ellen Brandmeyer, Institute for the Environment
# create a grid and load it into a new table in the surrogates database, conus36km_062016 schema
# grid can be rectangular (xcellsize, ycellsize) instead of square (cellsize)
# conus12km_082016 schema
 
 dbname=$DBNAME
 schemaname=public
 server=$DBSERVER
 user=$PG_USER

######################################################################
# '4CALIF1'
# 'LAM_40N97W'  -2400000.000  -732000.000      4000.000      4000.000 225 325   1 

#  tblname='calif4k_225x325'   # name of table in schema

#  proj=900921                  # value of srid in spatial_ref_sys data table
#  xorig=-2400000               # x-coordinate of lower-left corner (LLC) of grid
#  yorig=-732000                # y-coordinate of LLC of grid
#  xcellsize=4000               # cell size in x-direction
#  ycellsize=4000               # cell size in y-direction
#  cols=225                     # number of columns (x)
#  rows=325                     # number of rows (y)

######################################################################
# 'HEMI108_187x187'
# 'POLAR_HEMI'  -10098000.000  -10098000.000    108000.000    108000.000 187 187   1

proj=900915
xorig=-10098000
yorig=-10098000
xcellsize=108000
ycellsize=108000
cols=187
rows=187
 
tblname='hemi108k_187x187'


######################################################################
# begin new section

#'4US3'
#'LAM_40N97W' -2952000.000  -2772000.000      4000.000      4000.000 1548 1332   1

#tblname='us4k_1548x1332'       # name of table in schema

#proj=900921                    # value of srid in spatial_ref_sys data table
#xorig=-2952000                 # x-coordinate of lower-left corner (LLC) of grid
#yorig=-2772000                 # y-coordinate of LLC of grid
#xcellsize=4000                 # cell size in x-direction
#ycellsize=4000                 # cell size in y-direction
#cols=1548                      # number of columns (x)
#rows=1332                      # number of rows (y)

# do not modify below this line
######################################################################

echo " ==== Creating $schemaname.$tblname"
psql -h $server -U $user -q $dbname << END
DROP TABLE IF EXISTS $schemaname.$tblname;
CREATE TABLE $schemaname.$tblname (
  colnum INT NOT NULL,
  rownum INT NOT NULL,
  gridcell geometry(Polygon, $proj),
  PRIMARY KEY (colnum, rownum)
);
CREATE INDEX ON $schemaname.$tblname USING GIST (gridcell);
INSERT INTO $schemaname.$tblname
(SELECT (gv).x colnum, (gv).y colnum, (gv).geom
FROM (SELECT ST_PixelAsPolygons(
  ST_AddBand(ST_MakeEmptyRaster($cols, $rows, $xorig, $yorig, $xcellsize, $ycellsize, 0, 0, $proj), '8BUI'::text, 1, 0)
) gv) v
);
END

