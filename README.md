# Isilon Data Insights Connector
The isi_data_insights_d.py script controls a daemon process that can be used to query multiple OneFS clusters for statistics data via the Isilon's Platform API (PAPI). It then has a pluggable module for processing the results of those queries. An example stat processor that sends the query results to an instance of InfluxDB is provided along with an example Grafana dashboard.

# Install Dependencies Locally
* sudo pip install -r requirements.txt
* Install Isilon SDK Python language bindings for OneFS 8.0.X or 7.2.X (see SDK version help below).
* Instructions for 8.0 SDK: https://github.com/Isilon/isilon_sdk_8_0_python#requirements
* Instructions for 7.2 SDK: https://github.com/Isilon/isilon_sdk_7_2_python/#requirements.

# Install Virtual Environment
* To install the connector with the 8.0 SDK run (see SDK version help below):
```sh
./setup_venv.sh 8
```
* To install the connector with the 7.2 SDK run (see SDK version help below):
```sh
./setup_venv.sh 7
```

# Isilon SDK Python Bindings Version Help
* If you intend to monitor both 8.0 and 7.2 clusters with a single instance of the Isilon Data Insights Connector then you can install either the 8.0 SDK or the 7.2 SDK, but the 8.0 SDK is preferable because it is able to query multiple statistics more efficiently.
* However, before you decide, note that while the Connector's use of the 8.0 Python SDK is compatible with 7.2 clusters, the 8.0 SDK is not completely compatible with 7.2 clusters in general. So if you intend to use the SDK for other purposes, you may be better off doing two "Virtual Environment" installs, one with the 8.0 SDK installed and the other with the 7.2 SDK installed. Then each instance of the Connector can send statistics data to a single instance of InfluxDB. Doing this will still allow you to monitor multiple clusters via a single Grafana dashboard.
* If you intend to monitor only a single cluster or if all your clusters are either 8.0 or 7.2 then install the version of the SDK that matches the version of the clusters you intend to monitor.

# Run it
* Modify the provided configuration file (isi_data_insights_d.cfg) so that it points at the set of Isilon OneFS clusters that you want to query. If you are running multiple instances of the Connector via "Virtual Environment" then you will probably want to create a separate config file for each.
* The default configuration file is setup to send the stats data to InfluxDB via the influxdb_plugin.py. So if you intend to use the default plugin you will need to install InfluxDB, which you can do locally or remotely.
```sh
sudo apt-get install influxdb).
```
* If you installed InfluxDB to somewhere other than localhost and/or port 8086 then you'll also need to update the example configuration file with the address and port of the InfluxDB.
* If you did a "Virtual Environment" install then be sure to activate the venv by running:
```sh
. .venv8/bin/activate
```
or if you installed the 7.2 SDK run:
```sh
. .venv7/bin/activate
```
* Run it:
```sh
./isi_data_insights_d.py -c ./isi_data_insights_d.cfg start
```

# View the example Grafana dashboard
* Install and configure Grafana to use the InfluxDB that you installed previously.
* Import the example Grafana dashboard: isi_data_insights_grafana_dashboard.gcfg

# Write your own custom stats processor
* If you would like to process the stats data differently than the provided influxdb_plugin.py then you can implement a custom stats processor.
* Create a file called my_plugin.py that defines a process(cluster, stats) function that takes as input the name of a cluster and a list of isi_sdk/models/CurrentStatisticsStat objects.
* Optionally define a start(argv) function that takes a list of input args as defined in the config file via the stats_processor_args parameter.
* Optionally define a stop() function.
* Put the my_plugin.py file somewhere in your PYTHONPATH.
* Update the isi_data_insights_d.cfg file with the name of your plugin (i.e. 'my_plugin')
* Restart the isi_data_insights_d.py daemon:
```sh
./isi_data_insights_d.py -c ./isi_data_insights_d.cfg restart
```
