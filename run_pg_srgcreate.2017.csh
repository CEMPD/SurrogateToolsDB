#!/bin/csh -f

source pg_setup.csh
java -classpath ./SurrogateTools-2.2.jar gov.epa.surrogate.ppg.Main control_variables_pg.2017.csv

#merge and gapfilling
#java -classpath ./SurrogateTools-2.2.jar gov.epa.surrogate.SurrogateTool control_variables_pg.2017.csv

