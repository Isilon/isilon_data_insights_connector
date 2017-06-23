# Isilon Data Insights Connector
The isi_data_insights_d.py script controls a daemon process that can be used to query multiple OneFS clusters for statistics data via the Isilon's Platform API (PAPI). It then has a pluggable module for processing the results of those queries. The provided stat processor, defined in influxdb_plugin.py, sends query results to an InfluxDB backend. Additionally, several Grafana dashboards are provided to make it easy to monitor the health and status of your Isilon clusters.

# Installation Instructions
For detailed instructions on setting up a VM and installing the Connector on the VM refer to:

https://community.emc.com/blogs/keith/2017/01/26/isilon-data-insights-connector--do-it-yourself-isilon-monitoring

For a bit less detail, perhaps quicker setup, refer to the instructions below.

There are three ways to install the Connector. The local installation simply installs the required Python dependencies on the local system. The virtual environment installation installs the required Python dependencies in what's known as a Python Virtual Environment (see http://docs.python-guide.org/en/latest/dev/virtualenvs/). In both cases the Connector is designed to run directly from the source directory.

# Local Installation Instructions
* sudo pip install -r requirements.txt

# Virtual Environment Installation Instructions
* To install the connector in a virtual environment run:
```sh
./setup_venv.sh
```

# Run Instructions
* Rename or copy the example configuration file, example_isi_data_insights_d.cfg, to isi_data_insights_d.cfg. The path ./isi_data_insights_d.cfg is the default configuration file path for the Connector. If you use that name and run the Connector from the source directory then you don't have to use the --config parameter to specify a different configuration file.
* Next edit the isi_data_insights_d.cfg so that it is setup to query the set of Isilon OneFS clusters that you want to monitor, do this by modifying the config file's clusters parameter.
* The example configuration file is pre-setup to send several sets of stats to InfluxDB via the influxdb_plugin.py. So if you intend to use the default plugin you will need to install InfluxDB. InfluxDB can be installed locally (i.e on the same system as the Connector) or remotely (i.e. on a different system).
```sh
sudo apt-get install influxdb
```
* If you installed InfluxDB to somewhere other than localhost and/or port 8086 then you'll also need to update the configuration file with the address and port of the InfluxDB.
* If you did a "Virtual Environment" install then be sure to activate it before running the Connector. Activate the venv by running:
```sh
. .venv/bin/activate
```
* To run the Connector:
```sh
./isi_data_insights_d.py start
```

# Grafana Setup
Included with the Connector source code are three Grafana dashboards that make it easy to monitor the health and status of your Isilon clusters. To view the dashboards with Grafana, follow these instructions:
* <a href='http://docs.grafana.org/installation/' taget='_blank'>Install and configure Grafana</a> to use the InfluxDB as a data source. Note that the provided Grafana dashboards have been tested to work with Grafana version 4.3.1. Also, note that the influxdb_plugin.py creates and stores the statistic data in a database named isi_data_insights. You'll need that information when following the instructions for adding a data source to Grafana. Also, be sure to configure the isi_data_insights data source as the default Grafana data source using the Grafana Dashboard Admin web-interface.
* Import the Grafana dashboards.
 * grafana_cluster_list_dashboard.json
![Multi-cluster Summary Dashboard Screen Shot](https://raw.githubusercontent.com/Isilon/isilon_data_insights_connector/master/IsilonDataInsightsMultiClusterSummary.JPG) 
 * grafana_cluster_capacity_utilization_dashboard.json
 ![Cluster Capacity Utilization Dashboard Screen Shot](https://raw.githubusercontent.com/Isilon/isilon_data_insights_connector/master/IsilonDataInsightsClusterCapacityUtilizationTable.JPG)
 * grafana_cluster_detail_dashboard.json
 ![Cluster Detail Dashboard Screen Shot](https://raw.githubusercontent.com/Isilon/isilon_data_insights_connector/master/IsilonDataInsightsClusterDetail.JPG)
 * grafana_cluster_protocol_dashboard.json
![Cluster Protocol Detail Dashboard Screen Shot](https://raw.githubusercontent.com/Isilon/isilon_data_insights_connector/master/IsilonDataInsightsClusterProtocolDetail.JPG)

Import (optional) HDFS specific dashboards:
* grafana_hadoop_home.json
![Hadoop Home Dashboard Screeenshot](https://github.com/isilon/isilon_data_insights_connector/blob/master/HDFS-home-1.png)
* grafana_hadoop_datanodes.json
![Hadoop Home Dashboard Screeenshot](https://github.com/isilon/isilon_data_insights_connector/blob/master/HDFS-datanode-1.png)

* If you have already started the Connector then there should be data already in your database and displayed in the dashboards. One common issue that might prevent your dashboards from showing up correctly, is that the date/time on your Isilon clusters is not closely enough in-synch with the date/time used by Grafana, synchronizing the date/time of all the systems to within a few seconds of each other should be enough to fix the issue.


# Kapacitor Integration
Kapacitor (https://www.influxdata.com/time-series-platform/kapacitor/) is an add-on component that when used in conjunction with the Connector enables flexible, configurable, real-time notifications of alert conditions based off the statistics data streaming into the InfluxDB. For more information on how to integrate the Connector and InfluxDB with Kapacitor refer to:

[Kapacitor Integration Instructions](https://github.com/Isilon/isilon_data_insights_connector/blob/master/README_KAPACITOR_INTEGRATION.md)

# Customizing the Connector
The Connector is designed to allow for customization via a plugin architecture. The default plugin, influxd_plugin.py, is configured via the provided example configuration file. If you would like to process the stats data differently or send them to a different backend than the influxdb_plugin.py you can implement a custom stats processor. Here are the instructions for doing so:
* Create a file called my_plugin.py, or whatever you want to name it.
* In the my_plugin.py file define a process(cluster, stats) function that takes as input the name/ip-address of a cluster and a list of stats. The list of stats will contain instances of the isi_sdk_8_0/models/CurrentStatisticsStat class or isi_sdk_7_2/models/CurrenStatisticsStat class, but it makes no difference because the two classes are the same regardless of the version.
* Optionally define a start(argv) function that takes a list of input args as defined in the config file via the stats_processor_args parameter.
* Optionally define a stop() function.
* Put the my_plugin.py file somewhere in your PYTHONPATH (easiest is to put into the same directory as the other Python source code files).
* Update the isi_data_insights_d.cfg file with the name of your plugin (i.e. 'my_plugin')
* Restart the isi_data_insights_d.py daemon:
```sh
./isi_data_insights_d.py restart
```
# Extending and/or Contributing to the Connector

There are multiple ways for anyone using the Connector to interact with our dev team to request new features or discuss problems.

* Create a new issue on the “Issues” section.   https://github.com/Isilon/isilon_data_insights_connector/issues
* The “Discussion” capability of the Isilon SDK Info Hub page.  https://community.emc.com/docs/DOC-48273
* The most effective is the #isilon channel on codecommunity.slack.com.

Also, just like an other project on github.com we are entirely open to external code contributions:

* Fork the project, modify it, then initiate a pull request.
