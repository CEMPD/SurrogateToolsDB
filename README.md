# Surrogate Tools DB Quickstart Guide
Last updated: 11/10/2021

Note that Surrogate Tools DB currently only supports creating surrogates for regular grids. For E-Grid or census track (polygon) surrogates, please use the Spatial Allocator (https://www.cmascenter.org/sa-tools/).

0. Prerequisites
   - Install the Postgres database server and the PostGIS extension. Make sure the Postgres server is running.
   - Create a new database for the surrogates work. This document uses a database named "surrogates" by default. The following commands can be executed from the `psql` command line, and will create a database named "surrogates", connect to that database, and add the PostGIS extension.
   ```
   CREATE DATABASE surrogates;
   \c surrogates
   CREATE EXTENSION postgis;
   ```
   - For PostGIS version 3 or newer, add the separate PostGIS raster extension.
   ```
   CREATE EXTENSION postgis_raster;
   ```

   - For the surrogate tool to run, you will need to have a database user with all privileges on the new database. The following `psql` commands create a new user named "pgsurg" and assign the appropriate privileges.
   ```
   CREATE USER pgsurg WITH PASSWORD '<password>';
   GRANT ALL PRIVILEGES ON DATABASE surrogates TO pgsurg;
   GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO pgsurg;
   ```

   - Install the Java Runtime Environment (if needed).
   - Install the tcsh package (if needed).
   - Download the Surrogate Tools DB package. This guide uses the installation location /opt/srgtool/.

1. Update "pg_setup.csh" (located in /opt/srgtool/) for your server and then run `source pg_setup.csh`.
   ```
   setenv SRG_HOME /opt/srgtool
   setenv PGBIN    /usr/bin        # location of Postgres tools (psql)
   setenv PG_USER  pgsurg
   setenv DBNAME   surrogates
   setenv DBSERVER localhost
   setenv GDALBIN  /usr/gdal32/bin # location of GDAL tools (ogr2ogr)
   ```
   To avoid being asked repeatedly for the Postgres user account password, you can create a password file in your home directory. See the Postgres documentation, https://www.postgresql.org/docs/current/libpq-pgpass.html, for more details.

2. Download the archive shapefiles.quickstart.v1_1.tar.gz (809 MB) from https://drive.google.com/drive/folders/1idGoi6I3GvKFCcf87O_8zMirM7Gtd0_E and unpack it in /opt/srgtool/data/. The full unpacked archive is about 1.5 GB.
   ```
   cd /opt/srgtool/data
   tar xvf shapefiles.quickstart.v1_1.tar.gz
   ```

3. Add the output modeling projection to the Postgres database.
   ```
   cd /opt/srgtool/util
   psql -h $DBSERVER -d $DBNAME -U $PG_USER -f create_900921.sql
   ```
   This command will add a new Lambert conformal conic projection with the ID 900921 to the spatial_ref_sys table in the database.

4. Load the shapefiles into tables in the database.
   ```
   cd /opt/srgtool/util
   ./load_shapefile_reproject_multi.quickstart.csh
   ```
   This script will load the county boundaries shapefile (cb_2017_us_county_500k) and the population and housing shapefile (acs2016_5yr_bg) into the database.

5. Create a database table representing the modeling grid.
   ```
   cd /opt/srgtool/util
   ./generate_modeling_grid.sh
   ```
   This script creates a 12 km grid using the Lambert projection added earlier.

6. Update the settings files in /opt/srgtool/ used by the surrogate tool (if needed).
   - control_variables_pg.quickstart.csv
   
   | Setting | Default value | Description |
   | - | - | - |
   | PG_SERVER | localhost | Database host |
   | PG_USER | pgsurg | Postgres username |
   | DBNAME | surrogates | Database name |
   | PGBIN | /usr/bin | Location of Postgres executables |
   | OUTPUT DIRECTORY | ./outputs/us12k_516x444 | Directory for individual surrogate files |
   | LOG FILE NAME | ./LOGS/srg_12US3.log | Log file to store all information from running the program |

   - surrogate_generation_pg.quickstart.csv: Specifies which surrogates will be created. For this guide, only the population surrogate (code 100) will be generated.
   
   - surrogate_specification_pg.quickstart.csv: Details how each surrogate should be created, including which data and weight shapefiles should be used, which attributes to use, and any weighting or filtering functions that should be applied.

7. Run Surrogate Tools DB to generate surrogates.
   ```
   cd /opt/srgtool
   ./run_pg_srgcreate.quickstart.csh
   ```
   Various messages will be displayed as the tool runs. If there are no problems encountered, the last message will be
   ```
   SUCCESS -- The Program Run Completed. See log file for details.
   ```
   and you can continue to Step 8. If there is a problem, the last message will be
   ```
   ERROR -- The Program Run Stopped. See log file for details.
   ```
   The log file location is controlled by the LOG FILE NAME setting in the control_variables_pg.quickstart.csv file. If you didn't change this in Step 6, the default location is /opt/srgtool/pg_srgtools/LOGS/.

8. Compare your outputs to the sample outputs using the `diffsurr` tool.
   ```
   cd /opt/srgtool
   ./bin/64bits/diffsurr.exe outputs/us12k_516x444/USA_100_NOFILL.txt 100 outputs/us12k_516x444_example/USA_100_NOFILL.txt 100 0.000001
   ```
   If the newly generated surrogates match the sample outputs, you'll see the message "The surrogate comparison was successful!"

## How-Tos

### Generate additional surrogates

After successfully generating the population surrogate following the quickstart guide, you can use the sample shapefiles you downloaded to generate a housing surrogate. Edit the file named surrogate_generation_pg.quickstart.csv and find the column labeled GENERATE. For the population surrogate, change GENERATE to NO; for the housing surrogate, change GENERATE to YES.

```
REGION,SURROGATE,SURROGATE CODE,GENERATE,QUALITY ASSURANCE,PG SCRIPT
USA,Population,100,NO,YES,template_polygon_noFF_withWA.csh
USA,Housing,110,YES,YES,template_polygon_noFF_withWA.csh
```

After updating the surrogate_generation_pg.quickstart.csv file, run Surrogate Tools DB to generate the surrogates.

```
cd /opt/srgtool
./run_pg_srgcreate.quickstart.csh
```

To compare your outputs to the sample outputs, use the following `diffsurr` command.

```
cd /opt/srgtool
./bin/64bits/diffsurr.exe outputs/us12k_516x444/USA_110_NOFILL.txt 110 outputs/us12k_516x444_example/USA_110_NOFILL.txt 110 0.000001
```

### Download additional shapefiles

Archives containing all the shapefiles used in EPA's 2016v1 and 2017/2016v2 emissions modeling platforms are available for download at https://drive.google.com/drive/folders/1idGoi6I3GvKFCcf87O_8zMirM7Gtd0_E

### Generate surrogates using EPA's 2017/2016v2 emissions modeling platform shapefiles

1. Download the shapefiles from the Google Drive link listed above
2. To load the shapefiles, use the script util/load_shapefile_reproject_multi.2017.csh
3. Update the settings file control_variables_pg.2017.csv if needed
4. Run the script run_pg_srgcreate.2017.csh

Note that the file surrogate_generation_pg.2017.csv only has GENERATE set to YES for the population surrogate by default. To generate additional surrogates, set the GENERATE column to YES as needed.

### Generate surrogates using EPA's 2016v1 emissions modeling platform shapefiles

1. Download the shapefiles from the Google Drive link listed above
2. To load the shapefiles, use the script util/load_shapefile_reproject_multi.2016v1.csh
3. Update the settings file control_variables_pg.2016v1.csv if needed
4. Run the script run_pg_srgcreate.2016v1.csh

Note that the file surrogate_generation_pg.2016v1.csv only has GENERATE set to YES for the population surrogate by default. To generate additional surrogates, set the GENERATE column to YES as needed.

### Using a different grid with the same projection

1. Update the script util/generate_modeling_grid.sh to define the new grid parameters.
2. Update the control variables file (e.g. control_variables_pg.quickstart.csv) to set the grid name, output directory, and log file name.
3. Generate the surrogates.

### If the grid is on a new projection

1. Add the projection to the database. The script util/create_900921.sql shows how the LAM_40N97W projection used in the CONUS domains is created. The srid field for the new projection needs to be unique in the database.
2. Load the shapefiles using the new projection. Existing shapefile tables need to be deleted (or renamed). Update the script util/load_shapefile_reproject_multi.quickstart.csh with the new srid number.
3. Update the script util/generate_modeling_grid.sh to define the new grid parameters, making sure to set the projection.
4. Update the control variables file (e.g. control_variables_pg.quickstart.csv) to set the grid name, output directory, and log file name.
5. Generate the surrogates.

### Using non-rectangular grids

For non-rectangular grids, as long as the polygons are defined in a table similar to the one created by util/generate_modeling_grid.sh, things should work.
