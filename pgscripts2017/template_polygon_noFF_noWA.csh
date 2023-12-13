#!/bin/csh -fx
# acs_2014_grid_script.sh
# AFTER sucessful completion of acs_2014_prcs_script.sh; performs grid overlay and related calculations
# handles reprojections of geometry objects (geographic polygons), vacuum, analyze, and cluster as appropriate

VAR_DEFINITIONS

setenv cluster false					
setenv shp_tbl_d `echo $data_shape | tr "[:upper:]" "[:lower:]"` 
setenv shp_tbl_w `echo $weight_shape | tr "[:upper:]" "[:lower:]"` 
set data_table=$schema.${shp_tbl_d}
set weight_table=$schema.${shp_tbl_w}
set grid_table=${schema_name}.${grid}
set geom_grid=$grid_table.gridcell
set geom_data=${data_table}.geom_${srid_final}
set geom_weight=${weight_table}.geom_${srid_final}

set geom_d = "d.geom_${srid_final}"
set geom_w = "w.geom_${srid_final}"

echo $data_table
# cut with geographic boundaries
cat << ieof > ${output_dir}/temp_files/${surg_code}_create_wp_cty.sql
-- Cutting by data shapefile boundaries
DROP TABLE IF EXISTS ${schema_name}.wp_cty_${surg_code}_${srid_final};
CREATE TABLE ${schema_name}.wp_cty_${surg_code}_${srid_final} (
  ${data_attribute} varchar (6) not null,
  area_${srid_final} double precision default 0.0);
SELECT AddGeometryColumn('${schema_name}', 'wp_cty_${surg_code}_${srid_final}', 'geom_${srid_final}', ${srid_final}, 'MultiPolygon', 2);

WITH ge AS (
  SELECT ST_SetSRID(ST_Extent(gridcell),${srid_final}) AS extent 
  FROM ${grid_table})
, d AS (
  SELECT ${data_table}.* 
  FROM ${data_table} JOIN ge
  ON ST_Intersects(${geom_data}, ge.extent))
, de AS (
  SELECT ST_SetSRID(ST_Extent(${geom_d}),${srid_final}) AS extent 
  FROM d)
, w AS (
  SELECT ${weight_table}.* 
  FROM ${weight_table} JOIN de
  ON ST_Intersects(${geom_weight}, de.extent))
INSERT INTO ${schema_name}.wp_cty_${surg_code}_${srid_final}
  SELECT 
    d.${data_attribute},
    0.0,
    CASE
      WHEN ST_CoveredBy(${geom_w},${geom_d})
        THEN ${geom_w}
      ELSE
        ST_CollectionExtract(ST_Multi(ST_Intersection(${geom_w},${geom_d})), 3)
    END AS geom_${srid_final}
  FROM d
  JOIN w
  ON ( ST_Intersects(${geom_w},${geom_d})
  AND NOT ST_Touches(${geom_w},${geom_d}));
UPDATE ${schema_name}.wp_cty_${surg_code}_${srid_final}
  SET geom_${srid_final} = ST_MakeValid(geom_${srid_final}) WHERE NOT ST_IsValid(geom_${srid_final});
UPDATE  ${schema_name}.wp_cty_${surg_code}_${srid_final} 
  SET area_${srid_final}=ST_Area(geom_${srid_final});
CREATE INDEX ON ${schema}.wp_cty_${surg_code}_${srid_final} USING GIST(geom_${grid_proj});
VACUUM ANALYZE ${schema_name}.wp_cty_${surg_code}_${srid_final};
ieof

echo "Cutting by data shapefile boundaries"
$PGBIN/psql -h $server -d $dbname -U $user -f ${output_dir}/temp_files/${surg_code}_create_wp_cty.sql

# create query to grid weight data
cat << ieof > ${output_dir}/temp_files/${surg_code}_create_wp_cty_cell.sql
-- Gridding weight data to modeling domain
DROP TABLE IF EXISTS wp_cty_cell_${surg_code}_${grid};
CREATE TABLE ${schema_name}.wp_cty_cell_${surg_code}_${grid} (
  ${data_attribute} varchar (6) not null,
  colnum integer not null,
  rownum integer not null,
  area_${srid_final} double precision default 1.0);
SELECT AddGeometryColumn('${schema_name}', 'wp_cty_cell_${surg_code}_${grid}', 'geom_${srid_final}', ${srid_final}, 'MultiPolygon', 2);
	
INSERT INTO ${schema_name}.wp_cty_cell_${surg_code}_${grid}
  SELECT ${data_attribute}, colnum, rownum,
    0.0,
    CASE
      WHEN ST_CoveredBy(${schema}.wp_cty_${surg_code}_${srid_final}.geom_${grid_proj}, ${grid_table}.gridcell)
        THEN wp_cty_${surg_code}_${srid_final}.geom_${grid_proj}
      ELSE
        ST_CollectionExtract(ST_Multi(ST_Intersection(${schema}.wp_cty_${surg_code}_${srid_final}.geom_${grid_proj}, ${grid_table}.gridcell)),3)
    END AS geom_${srid_final}
  FROM ${schema}.wp_cty_${surg_code}_${srid_final}
  JOIN ${grid_table}
  ON ( ST_Intersects(${schema}.wp_cty_${surg_code}_${srid_final}.geom_${grid_proj}, ${grid_table}.gridcell)
  AND NOT ST_Touches(${schema}.wp_cty_${surg_code}_${srid_final}.geom_${grid_proj}, ${grid_table}.gridcell));
UPDATE ${schema_name}.wp_cty_cell_${surg_code}_${grid}
  SET geom_${srid_final} = ST_MakeValid(geom_${srid_final}) WHERE NOT ST_IsValid(geom_${srid_final});
UPDATE ${schema_name}.wp_cty_cell_${surg_code}_${grid} 
  SET area_${srid_final}=ST_Area(geom_${srid_final});
CREATE INDEX ON $schema.wp_cty_cell_${surg_code}_${grid} USING GIST(geom_${grid_proj});
VACUUM ANALYZE ${schema_name}.wp_cty_cell_${surg_code}_${grid};
ieof

echo "Gridding weight data to modeling domain"
$PGBIN/psql -h $server -d $dbname -U $user -f ${output_dir}/temp_files/${surg_code}_create_wp_cty_cell.sql

# Create numerater table
cat << ieof > ${output_dir}/temp_files/${surg_code}_numer.sql
-- CREATE TABLE $schema.numer_${surg_code}_${grid}
DROP TABLE IF EXISTS $schema.numer_${surg_code}_${grid};
CREATE TABLE $schema.numer_${surg_code}_${grid} (
  $data_attribute varchar(5) not null,
  colnum integer not null,
  rownum integer not null,
  numer double precision,
  primary key ($data_attribute, colnum, rownum));
INSERT INTO $schema.numer_${surg_code}_${grid}
  SELECT 
    $data_attribute,
    colnum,
    rownum,
    SUM(area_${srid_final}) AS numer
  FROM $schema.wp_cty_cell_${surg_code}_${grid}
  GROUP BY $data_attribute, colnum, rownum;
ieof
echo "CREATE TABLE $schema.numer_${surg_code}_${grid}"
$PGBIN/psql -h $server -d $dbname -U $user -f ${output_dir}/temp_files/${surg_code}_numer.sql

# Calculate donominator
cat << ieof > ${output_dir}/temp_files/${surg_code}_denom.sql
-- CREATE TABLE $schema.denom_${surg_code}_${grid}; create primary key
DROP TABLE IF EXISTS $schema.denom_${surg_code}_${grid};
CREATE TABLE $schema.denom_${surg_code}_${grid} (
  $data_attribute varchar(5) not null,
  denom double precision,
  primary key ($data_attribute));
INSERT INTO $schema.denom_${surg_code}_${grid}
  SELECT $data_attribute,
    SUM(area_${srid_final}) AS denom
  FROM $schema.wp_cty_${surg_code}_${srid_final}
  GROUP BY $data_attribute;
ieof
echo "CREATE TABLE $schema.denom_${surg_code}_${grid}; create primary key"
$PGBIN/psql -h $server -d $dbname -U $user -f ${output_dir}/temp_files/${surg_code}_denom.sql

# Calculate surrogate
cat << ieof > ${output_dir}/temp_files/${surg_code}_surg.sql
-- CREATE TABLE $schema.surg_${surg_code}_${grid}; add primary key
DROP TABLE IF EXISTS $schema.surg_${surg_code}_${grid};
CREATE TABLE $schema.surg_${surg_code}_${grid} (
  surg_code integer not null,
  $data_attribute varchar(5) not null,
  colnum integer not null,
  rownum integer not null,
  surg double precision,
  numer double precision,
  denom double precision,
  primary key ($data_attribute, colnum, rownum));
INSERT INTO $schema.surg_${surg_code}_${grid}
  SELECT 
    CAST('$surg_code' AS INTEGER) AS surg_code,
    d.$data_attribute,
    colnum,
    rownum,
    numer / denom AS surg,
    numer,
    denom
  FROM $schema.numer_${surg_code}_${grid} n
  JOIN $schema.denom_${surg_code}_${grid} d
  USING ($data_attribute)
   WHERE numer != 0
     AND denom != 0
  GROUP BY d.$data_attribute, colnum, rownum, numer, denom
  ORDER BY d.$data_attribute, colnum, rownum;
ieof
echo "CREATE TABLE $schema.surg_${surg_code}_${grid}; add primary key"
$PGBIN/psql -h $server -d $dbname -U $user -f ${output_dir}/temp_files/${surg_code}_surg.sql

# Export surrogate
echo "Exporting surrogates $schema.surg_${surg_code}_${grid}; "
echo "#GRID" > ${output_dir}/USA_${surg_code}_NOFILL.txt
$PGBIN/psql -h $server -d $dbname -U $user --field-separator '	' -t --no-align << END >> ${output_dir}/USA_${surg_code}_NOFILL.txt

SELECT surg_code, ${data_attribute}, colnum, rownum, ROUND(surg::NUMERIC, 10), '!', numer, denom
  FROM $schema.surg_${surg_code}_${grid}
  order by ${data_attribute}, colnum, rownum;
END
