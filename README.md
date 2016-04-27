# Isilon Data Insights Connector
The isi_data_insights_d.py script controls a daemon process that can be used to query multiple OneFS clusters for statistics data via the Isilon's Platform API (PAPI). It then has a pluggable module for processing the results of those queries. An example stat processor that sends the query results to an instance of InfluxDB is provided along with an example Grafana dashboard.

# Install Dependencies Locally
* sudo pip install -r requirements.txt
* Install Isilon SDK from github.

# Install Virtual Environment
* ./setup_venv.sh
* Install Isilon SDK from github.

# Run it
* Install InfluxDB (i.e. sudo apt-get install influxdb).
* Modify the provided configuration file (isi_data_insights_d.cfg) so that it points at the set of Isilon OneFS clusters that you want to query.
* If you installed InfluxDB to somewhere other than localhost and/or port 8086 then you'll also need to update the example configuration file with the address and port of the InfluxDB.
* Run it: ./isi_data_insights_d.py -c ./isi_data_insights_d.cfg start

# View the example Grafana dashboard
* Install and configure Grafana to use the InfluxDB that you installed previously.
* Import the example Grafana dashboard: isi_data_insights_grafana_dashboard.gcfg

# Write your own custom stats processor
* Create a file called my_plugin.py that defines a process(cluster, stats) function that takes as input the name of a cluster and a list of isi_sdk/models/CurrentStatisticsStat objects.
* Optionally define a start(argv) function that takes a list of input args as defined in the config file via the stats_processor_args parameter.
* Optionally define a stop() function.
* Put the my_plugin.py file somewhere in your PYTHONPATH.
* Update the isi_data_insights_d.cfg file with the name of your plugin (i.e. 'my_plugin')
* Restart the isi_data_insights_d.py daemon: ./isi_data_insights_d.py -c ./isi-data_insights_d.cfg restart
