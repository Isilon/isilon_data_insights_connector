# Isilon Data Insights Connector
The isi_data_insights_d.py script controls a daemon process that can be used to query multiple OneFS clusters for statistics data via the Isilon's Platform API (PAPI). It then has a pluggable module for processing the results of those queries. An example stat processor that sends the query results to an instance of InfluxDB is provided along with an example Grafana dashboard.

# Install Dependencies Locally
* sudo pip install -r requirements.txt
* Install Isilon SDK Python language bindings for OneFS 8.0.X and/or 7.2.X (see SDK version help below).
* Instructions for SDK installation: https://github.com/Isilon/isilon_sdk

# Install Virtual Environment
* To install the connector in a virtual environment run:
```sh
./setup_venv.sh
```

# Isilon SDK Python Bindings Version Help
* If you intend to monitor both 8.0 and 7.2 clusters with a single instance of the Isilon Data Insights Connector then you should install both the 8.0 SDK and the 7.2 SDK. However, it should be noted that the 7.2 version of the SDK will work with 8.0 clusters, but not as efficiently (at least in the case of the StatisticsApi), so it is possible, but not recommended, to use the Connector with 8.0 clusters and the 7.2 SDK.
* If you intend to monitor only a single cluster or if all your clusters are either 8.0 or 7.2 then you should install the version of the SDK that matches the version of the clusters you intend to monitor.

# Run it
* Modify the provided configuration file (isi_data_insights_d.cfg) so that it points at the set of Isilon OneFS clusters that you want to query.
* The default configuration file is setup to send the stats data to InfluxDB via the influxdb_plugin.py. So if you intend to use the default plugin you will need to install InfluxDB. InfluxDB can be installed locally (i.e on the same system as the Connector) or remotely (i.e. on a different system).
```sh
sudo apt-get install influxdb
```
* If you installed InfluxDB to somewhere other than localhost and/or port 8086 then you'll also need to update the example configuration file with the address and port of the InfluxDB.
* If you did a "Virtual Environment" install then be sure to activate the venv by running:
```sh
. .venv/bin/activate
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
