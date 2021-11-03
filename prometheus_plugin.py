from __future__ import print_function
from future.utils import string_types
import logging
import time
import sys
import prometheus_client as prom
LOG = logging.getLogger(__name__)

# module variables
this = sys.modules[__name__]
collection_duration = None
gobaltags = {}
tagnames = []
intervalstart = 0
metriclist = {}

def start(argv):
    '''
    Setup Prometheus client interface.
    For prometheus all metrics are exposed via HTTP and the server will
    scrape (=collect) data from there

    Arguments:
        argv[0] = <port> (String)
            Default is 8080. If running inside containers do not change this port
            but instead change the exposed port via the docker run command
        argv[1] = <customtags> (String)
            Custom tags that are used to decorate metrics. The plugin needs to
            know them at startup time.
            Comma separated pairs like, group=Lab,datacenter=Berlin,....
    '''
    port = 8080
    this.globaltags = {}
    this.tagnames = []
    if isinstance(argv, list) and len(argv) > 0:
        port = int(argv[0])
        if len(argv) > 1:
            for item in argv[1].split(','):
                (key, val) = item.split('=')
                this.globaltags[key] = val

    this.tagnames = ['hostname', 'node'] + list(this.globaltags.keys())
    this.collection_duration = prom.Gauge('isi_collector_duration_seconds', '', this.tagnames)
    prom.start_http_server(port)
    LOG.info('Exposing data for prometheus at port {}'.format(port))

def start_process(cluster):
    '''
    Start of a new collection interval
    '''
    LOG.info('Start processing prometheus metrics for {}'.format(cluster))
    this.intervalstart = time.time()

def end_process(cluster):
    '''
    End of a collection interval
    '''
    tags = this.globaltags.copy()
    tags['hostname'] = cluster
    tags['node'] = ''
    this.collection_duration.labels(**tags).set(time.time() - this.intervalstart)
    LOG.info('Done processing {} metrics for prometheus for {}'.format(len(this.metriclist), cluster))

def process_stat(cluster, stat):
    ''' Arguments:
        cluster(String) = isilon cluster hostname/ip
        stat(Object)
    '''
    if stat.error != None:
        return
    tags = this.globaltags.copy()
    tags['hostname'] = cluster
    tags['node'] = str(stat.devid)

    if isinstance(stat.value, list):
        _process_list(tags, stat.key, stat.value)

    elif isinstance(stat.value, dict):
        _process_dict(tags, stat.key, stat.value)

    else:
        _process_one_stat(tags, stat.key, stat.value)

def _process_list(tags, basekey, statlist):
    ''' list of stats (expected as list of dict) '''
    for elem in statlist:
        if isinstance(elem, dict):
            _process_dict(tags, basekey, elem)
        else:
            LOG.error('Unexpected list of non-dict element: {}={}'.format(basekey, elem))

def _process_dict(tags, basekey, statdict):
    ''' dictionary stats
    all number values in the dict are metrics. But it contains text members
    and fields named with 'id': Those are filtered out as tags
    '''
    for k in statdict.keys():
        if isinstance(statdict[k], string_types) or (k[-2:] == 'id' and isinstance(statdict[k], int)):
            tags[k] = statdict[k]
            del statdict[k]

    for k in statdict.keys():
        mname = basekey + '_' + k
        _process_one_stat(tags, mname, statdict[k])

def _process_one_stat(tags, metricname, value):
    ''' process one stat for prometheus.
    metrics are kept inside the process as list of gauges for prometheus to scrape
    '''
    m = metricname.replace('.', '_')
    if m in this.metriclist:
        metric = this.metriclist[m]
    else:
        metric = prom.Gauge('isilon_' + m, '', tags.keys())
        this.metriclist[m] = metric
    metric.labels(**tags).set(value)

