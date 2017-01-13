# Kapacitor Integration
Kapacitor (https://www.influxdata.com/time-series-platform/kapacitor/) is an add-on component that when used in conjunction with the Connector enables flexible, configurable, real-time notifications of alert conditions based off the statistics data streaming into the InfluxDB. Kapacitor leverages the ability to subscribe to updates to the InfluxDB database to provide this capability.

# Initial setup
First setup InfluxDB and the Data Insights Connector following the instructions outlined in the README.md file. Then follow these instructions to install and setup Kapacitor:

Install Kapacitor from https://www.influxdata.com/downloads/#kapacitor

The getting started page (https://docs.influxdata.com/kapacitor/v1.0/introduction/getting_started/) contains useful examples, but is not entirely pertinent to this use case since it is leveraging Telegraf to generate statistics. In this case, you already have sets of statistics (measurements) in InfluxDB being fed by the Connector. After you have installed Kapacitor then you will need to configure it.

The Kapacitor installation package already includes the configuration file (/etc/kapacitor/kapacitor.conf) so there is no need to generate one. Edit /etc/kapacitor/kapacitor.conf to change the alert provider configurations as necessary. For instance, to enable email alerts, find the section beginning “[smtp]” and modify the configuration to utilize and available SMTP provider.

# Kapacitor Scripting

## Introduction
Kapacitor uses one or more tasks that are defined using “TICK” scripts to control what data should be filtered, how it should be filtered, and what criteria to use to alert based off the data. The TICK scripts are a domain-specific language (DSL) and are somewhat tersely documented on the Kapacitor documentation site (https://docs.influxdata.com/kapacitor/v1.0/). This document presents some example scripts, and presents some patterns to enable more sophisticated criteria for alerting (e.g. moving average).

## How to create and enable a TICK task
Edit the script using your favorite text editor. It is suggested that the name of these scripts use that “.tick” extension e.g. “nfs_avg_lat_alert.tick”
Next install the script into Kapacitor using the CLI. The generic form of the command is:

```sh
kapacitor define <internal_kapacitor_name> -type stream -tick <path_to_tick script> -dbrp isi_data_insights.autogen
```

The internal name should be something descriptive. These examples only show the use of stream scripts, but note that Kapacitor can also perform batch-processing. The path to the script is obvious. The “-dbrp” argument specifies the InfluxDB “database retention policy”. Since we are using the Isilon data insights connector database, the correct value for our examples is “isi_data_insights.autogen”; this value would differ if a different source database were in use. If we are using “nfs_avg_lat_alert.tick” as our example script, then the command to define the task would be:
```sh
kapacitor define nfs_lat_alert -type stream -tick /root/nfs_avg_lat_alert.tick -dbrp isi_data_insights.autogen
```

Here is the “nfs_avg_lat_alert.tick” script:
```
stream
    // Select avg NFS3 proto response time
    |from()
        .database('isi_data_insights')
        .measurement('cluster.protostats.nfs.total')
    |eval(lambda: float("time_avg") / 1000.0)
     .as('time_ms')
    |groupBy('cluster')
    |alert()
        .id('{{ index .Tags "cluster" }}/{{ .Name }}')
        .message('Average value of {{ .ID }} is {{ .Level}} value: {{ index .Fields "time_ms" }}ms')
        .crit(lambda: "time_ms" > 50.0)
        .warn(lambda: "time_ms" > 20.0)
        // Only warn every 15 mins if we haven't changed state
        .stateChangesOnly(15m)
        // Whenever we get an alert write it to a file.
        .log('/tmp/alerts.log')
        .slack()
```
Breaking it down:
* This is a stream filter so it starts with “stream”.
* Next, the script specifies where to pulling its data from. In this case, the “isi_data_insights” database, which is the default database created and populated by the Connector. This script selects a single measurement: “cluster.protostats.nfs.total”, which are the totaled (clusterwide as opposed to node-specific) NFS3 protocol statistics.
* Next, the script specifies an “eval” node which takes the “time_avg” measurement for the operations, and divides it by 1000. Note that the statistics values are in microseconds. Hence, this node is converting the values to milliseconds.
* Next, the script uses a “groupby” node, that is using the measurement tag “cluster” because the statistics for each cluster are distinct (e.g. we don’t want a low value from one cluster resetting the alert threshold of another cluster).
* Finally, the “alert” node. This is quite detailed (see next section for details).

Alert node details:
* First it defines the alert id that appears in the messages. In this case it will be <clustername>/nfs_lat_alert
* Next it defines the format of the message that appears in the alert. “.Level” is the alert level (crit, warn, info, ok). We index into the fields of the measurement to extract the “time_ms” field we generated to show the actual time value.
* The “.crit” and “.warn” nodes define a Boolean lambda function that determines whether that alert level has been reached. In this case, we’re defining the critical level to be a latency of greater than 50ms, and the warning level to be a latency of greater than 20ms.
* Lastly, the “squelch” node makes it so that it the alert is triggered repeatedly every 15 minutes if the alert level hasn’t changed, so we don’t get spammed with messages every 30 seconds.
* The ”.log” node simply logs these alerts to a local file (useful for testing).
* In this case, the alert is configured to use the Slack channel. This can be changed to use “.email” if that has been configured in the /etc/kapacitor/kapacitor.conf file, or “.post” to use the HTML POST method on a given URL. Numerous other alert channels are available. See the Kapacitor documentation for details.

Provided the syntax is correct, and the correct command is used, the task should now be defined in Kapacitor. However, it won’t be enabled:
```sh
kapacitor list tasks
ID                Type      Status    Executing Databases and Retention Policies
nfs_lat_alert     stream    disabled  false     ["isi_data_insights"."autogen"]
```
To enable the task, simply type:
```sh
kapacitor enable nfs_lat_alert
```
The task should now be enabled:
```sh
kapacitor list tasks
ID                Type      Status    Executing Databases and Retention Policies
nfs_lat_alert     stream    enabled   true      ["isi_data_insights"."autogen"]
```
It’s possible to check the status of the task and see the results at each node in the script:
```sh
kapacitor show nfs_lat_alert
ID: nfs_lat_alert
Error:
Template:
Type: stream
Status: enabled
Executing: true
Created: 10 Aug 16 12:10 PDT
Modified: 16 Aug 16 06:40 PDT
LastEnabled: 16 Aug 16 06:40 PDT
Databases Retention Policies: ["isi_data_insights"."autogen"]
TICKscript:
stream
    // Select avg NFS3 proto response time
    |from()
        .database('isi_data_insights')
        .measurement('cluster.protostats.nfs.total')
    |eval(lambda: float("time_avg") / 1000.0)
        .as('time_ms')
    |groupBy('cluster')
    |alert()
        .id('{{ index .Tags "cluster" }}/{{ .Name }}')
        .message('Average value of {{ .ID }} is {{ .Level}} value: {{ index .Fields "time_ms" }}ms')
        .crit(lambda: "time_ms" > 50.0)
        .warn(lambda: "time_ms" > 20.0)
        // Only warn every 15 mins if we haven't changed state
        .stateChangesOnly(15m)
        // Whenever we get an alert write it to a file.
        .log('/tmp/alerts.log')
        .slack()

DOT:
digraph nfs_lat_alert {
graph [throughput="0.00 points/s"];

stream0 [avg_exec_time_ns="0" ];
stream0 -> from1 [processed="58279"];

from1 [avg_exec_time_ns="1.215s" ];
from1 -> eval2 [processed="58279"];

eval2 [avg_exec_time_ns="208.86s" eval_errors="0" ];
eval2 -> groupby3 [processed="58279"];

groupby3 [avg_exec_time_ns="28.392s" ];
groupby3 -> alert4 [processed="58279"];

alert4 [alerts_triggered="2457" avg_exec_time_ns="87.22134ms" crits_triggered="836" infos_triggered="0" oks_triggered="1008" warns_triggered="613" ];
}
```

This output shows that the script is working and triggering on events. The “DOT:” section can be rendered as a graph using the “GraphViz” package.

This initial script works well, but is rather simplistic and, in particular, will alert on momentary spikes in load which may not be desirable.

# Example TICK script patterns
This section describes some examples for different types of alerting scripts.

# Moving average of measurement
This is an example of a script that uses a moving window to average the statistic value over a recent window:
```
stream
    // Select avg NFS3 proto response time
    |from()
        .database('isi_data_insights')
        .measurement('cluster.protostats.nfs.total')
    |groupBy('cluster')
    |window()
        .period(10m)
        .every(1m)
    |mean('time_avg')
        .as('time_avg')
    |eval(lambda: float("time_avg") / 1000.0)
         .as('mean_ms')
        .keep('mean_ms', 'time_avg')
    |alert()
        .id('{{ index .Tags "cluster" }}/{{ .Name }}')
        .message('Windowed average of avg value of {{ .ID }} is {{ .Level}} value: {{ index .Fields "mean_ms" }}ms')
        .crit(lambda: "mean_ms" > 50.0)
        .warn(lambda: "mean_ms" > 25.0)
        // Only warn every 15 mins if we haven't changed state
        .stateChangesOnly(15m)
        // Whenever we get an alert write it to a file.
        .log('/tmp/alerts.log')
        .slack()
```

This script is similar to the previous script, but there are a few important differences:
* The “window” node generates a window of data. With the values specified, we will keep and output the last 10 minutes of data every minute.
* The window output is fed into a “mean” node that calculates the mean of the data fed (the last 10 minutes of data, in this case the “time_avg” field), and stores the result back as the “time_avg” field to be fed further down the pipeline.
* The “eval” node converts the microsecond average field to a new “mean_ms” field.
* The rest of the alert is similar to the previous example.

# Joining/alerting based off two different measurements
This script is an example. It alerts based off moving average, but only if the operation count is above a given threshold. It’s probably not safe to use this as the sole alerting mechanism because a deadlock (which will reduce the operation count to zero) won’t generate an alert. Additional scripts are provided below to look for deadlock events (“node.ifs.heat.deadlocked.total” measurement) and to alert if no data points have been collected in a configurable period.

```
// Alert based off mean NFS3 proto response time if work is actually happening

var timestream = stream
    |from()
        .database('isi_data_insights')
        .measurement('cluster.protostats.nfs.total')
    |groupBy('cluster')
    |window()
        .period(10m)
        .every(1m)
    |mean('time_avg')
        .as('time_avg')
    |eval(lambda: float("time_avg") / 1000.0)
         .as('mean_ms')

var opstream = stream
    |from()
        .database('isi_data_insights')
        .measurement('cluster.protostats.nfs.total')
    |groupBy('cluster')
    |window()
        .period(10m)
        .every(1m)
    |mean('op_rate')
        .as('op_rate')

timestream
    |join(opstream)
        .as('times', 'ops')
    |alert()
        .id('{{ index .Tags "cluster" }}/{{ .Name }}')
        .message('Cluster {{ index .Tags "cluster" }} is executing {{ index .Fields "ops.op_rate" }} NFSv3 operations per second and windowed average of avg value of {{ .Name }} is {{ .Level }} value: {{ index .Fields "times.mean_ms" }}ms')
        .crit(lambda: "ops.op_rate" > 1000 AND "times.mean_ms" > 25.0)
        .warn(lambda: "ops.op_rate" > 1000 AND "times.mean_ms" > 10.0)
        // .info(lambda: TRUE)
        // Only warn every 15 mins if we haven't changed state
        .stateChangesOnly(15m)
        // Whenever we get an alert write it to a file.
        .log('/tmp/alerts.log')
        .slack()
```

This script is significantly different to the previous examples. It uses variables to store the results of the two different streams that we sample, and then uses a “join” operation to create a stream with both sets of data for us to alert from.

# Deadman alert to warn if data collection fails
This script uses the Kapacitor “Deadman” node to warn when the collected/emitted point count falls below a defined threshold in a given period. Many of the statistics collected by the Connector are updated as frequently as every 30 seconds, but the overall collection period can be longer if many clusters are being monitored, if they are large, and/or if they are under heavy load. The script arbitrarily uses 5 minutes as the interval for this example.
```
// Deadman alert for cluster data collection
var data = stream
    |from()
        .database('isi_data_insights')
        .measurement('cluster.health')
        .groupBy('cluster')

data
    |deadman(1.0, 5m)
        .id ('Statistics data collection for cluster {{ index .Tags "cluster" }}')
        .slack()
```

This script will output alerts of the form:
Statistics collection for cluster logserver is dead: 0.0
or
Statistics collection for cluster logserver is alive: 1.0

# Deadlock event count alert
This script uses one of the OneFS filesystem “heat” statistics to look for high rates of deadlocks within the filesystem.
```
stream
    // Alert based off node heat stats
    |from()
        .database('isi_data_insights')
        .measurement('node.ifs.heat.deadlocked.total')
    |groupBy('cluster')
    |alert()
        .id('Deadlock event count')
        .message('Value of {{ .ID }} on cluster {{ index .Tags "cluster" }}, node {{ index .Tags "node" }} is {{ .Level }} value: {{ index .Fields "value" }}')
        .crit(lambda: "value" > 50.0)
        .warn(lambda: "value" > 10.0)
        // .info(lambda: TRUE)
        // Only warn every 15 mins if we haven't changed state
        .stateChangesOnly(15m)
        // Whenever we get an alert write it to a file.
        .log('/tmp/alerts.log')
        .slack()
```

# Other useful node types
Kapacitor offers a number of useful processing nodes to filter the data. Examples that are of particular interest are:
* Mean/median/mode – computes the various average types.
* Max/min – selects the largest/smallest point.
* MovingAverage – a relatively new function that would simplify our earlier example.
* Stddev – computes the standard deviation of points. Useful to detect anomalies.
* Sum – sums the points.
* Deadman - useful to alert if the collector fails for some reason. It alerts if the points per interval drops below a given threshold.
