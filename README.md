# isi_perfstatsd.py
The isi_perfstatsd.py script controls a daemon process that can be used to query OneFS clusters for statistics data (provided by the PAPI's /statistics/current end point). It then has a pluggable module for processing the results of those queries with one example processor that sends the results to an instance of InfluxDB.

Dependencies:
	pip install influxdb
	pip install daemons
	Unpack and install: cribsbiox.west.isilon.com:/ifs/EngCSE/papi_swagger_client_python.tgz
	You'll also need an instance of InfluxDB to send the stats data to.
	There's probably some other ones, but you'll find them as you go.


Example usage with config file:
    ./isi_perfstatsd.py  -c ./isi_perfstatsd.cfg  start

Example usage with command line args:
	./isi_perfstatsd.py -x influxdb_plugin -a "localhost 8086 isi_data_insights" -i 10.25.69.74 -s "node.rp.stats" -u 5 -s "cluster.node.list.all" -u 30 --pid-file ./isi_perfstatsd.pid --log-file ./isi_perfstatsd.log start
