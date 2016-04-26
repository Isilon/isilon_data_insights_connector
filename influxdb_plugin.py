from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBServerError, InfluxDBClientError
import getpass
import logging
import requests
import sys


# InfluxDBClient interface
g_client = None
LOG = logging.getLogger(__name__)

# Number of points to queue up before writing it to the database.
MAX_POINTS_PER_WRITE = 100
# separator used to concatenate stat keys with sub-keys derived from stats
# whose value is a dict or list.
SUB_KEY_SEPARATOR = "_"


def start(argv):
    """
    Instantiate an InfluxDBClient. The expected inputs are the host/address and
    port of the InfluxDB and the name of the database to use. If the database
    does not exist then it will be created. If the fourth arg is "auth" then it
    will prompt the user for the InfluxDB's username and password.
    """
    influxdb_host = argv[0]
    influxdb_port = int(argv[1])
    influxdb_name = argv[2]
    if len(argv) > 3:
        if argv[3] == "auth":
            influxdb_username = raw_input("InfluxDB username: ")
            influxdb_password = getpass.getpass("Password: ")
        else:
            print >> sys.stderr, "Invalid args provided to %s: %s "\
                    "(expected: 'auth', got: '%s')" % (__name__, str(argv),
                            argv[3])
            sys.exit(1)
    else:
        influxdb_username = "root"
        influxdb_password = "root"

    LOG.info("Connecting to: %s@%s:%d database:%s.",
            influxdb_username, influxdb_host, influxdb_port, influxdb_name)

    global g_client
    g_client = InfluxDBClient(host=influxdb_host, port=influxdb_port,
                              database=influxdb_name,
                              username=influxdb_username,
                              password=influxdb_password)

    create_database = True
    try:
        databases = g_client.get_list_database()
    except (requests.exceptions.ConnectionError, InfluxDBClientError) as exc:
        print >> sys.stderr, "Failed to connect to InfluxDB server at %s:%s "\
                "database: %s.\nERROR: %s" % (influxdb_host,
                        str(influxdb_port), influxdb_name, str(exc))
        sys.exit(1)

    for database in databases:
        if database["name"] == influxdb_name:
            create_database = False
            break

    if create_database is True:
        LOG.info("Creating database: %s.", influxdb_name)
        g_client.create_database(influxdb_name)


def process(cluster, stats):
    """
    Convert Isilon stat query results to InfluxDB points and send to the
    InfluxDB service. Organize the measurements by cluster and node via tags.
    """
    LOG.debug("Processing stats %d.", len(stats))
    tags = {"cluster": cluster}
    influxdb_points = []
    points_written = 0
    for stat in stats:
        if stat.devid != 0:
            tags["node"] = stat.devid
        # check if the stat query returned an error
        if stat.error is not None:
            LOG.error("Query for stat: '%s', returned error: '%s'.",
                    str(stat.key), str(stat.error))
            continue
        # Process stat and then write points if list is large enough. Note
        # that an individual stat might result in multiple points being added
        # to the points depending on the type of the stat's value.
        try:
            # the stat value's data type is variable depending on the key so
            # use eval() to convert it to the correct type
            eval_value = eval(stat.value)
            # convert tuples to a list
            if type(eval_value) == tuple:
                stat.value = list(eval_value)
            else:
                stat.value = eval_value
        except: # eval throws a myriad of exceptions, we need to catch them all
            # if it doesn't convert to a different type then it is a string
            # value, which does not really make sense for InfluxDB, but oh well.
            pass
        _process_stat(stat.key, stat.time, stat.value, tags, influxdb_points)
        num_points = len(influxdb_points)
        if num_points > MAX_POINTS_PER_WRITE:
            points_written += _write_points(influxdb_points, num_points)
            influxdb_points = []
    # send left over points to influxdb
    num_points = len(influxdb_points)
    if num_points > 0:
        points_written += _write_points(influxdb_points, num_points)
    LOG.debug("Done processing stats, wrote %d points.", points_written)


def _process_stat_dict(stat_key, stat_time, stat_value, tags, influxdb_points):
    """
    For each item in the dictionary create a separate measurement point for
    influxdb by concatenating the stat's key with the stat's key in the value
    dictionary.
    Append each point to the influxdb_points list.
    """
    # make a copy of the tags so that any string values in the dict can be
    # added as tags of the non-string values of this dict and any "id" keys
    # can be added as tags as well.
    dict_tags = tags.copy()
    for sub_key, value in stat_value.iteritems():
        stat_name = stat_key + SUB_KEY_SEPARATOR + sub_key
        if (type(value) == str or type(value) == unicode) \
                or (sub_key[-2:] == "id" and type(value) == int):
            dict_tags[sub_key] = value
        else:
            _process_stat(stat_name, stat_time, value, dict_tags,
                    influxdb_points)


def _process_stat_list(stat_key, stat_time, stat_value, tags, influxdb_points):
    """
    For each item in the dictionary create a separate measurement point for
    influxdb by concatenating the stat's key with index in the value list.
    Append each point to the influxdb_points list.
    """
    for index in range(0, len(stat_value)):
        list_value = stat_value[index]
        if type(list_value) != dict:
            # if it is not a dict then give it a unique name based on the index
            stat_name = stat_key + SUB_KEY_SEPARATOR + str(index)
            _process_stat(stat_name, stat_time, list_value, tags,
                    influxdb_points)
        else:
            # if it is a dict then the dict's keys will determine the names
            _process_stat_dict(stat_key, stat_time, list_value, tags,
                    influxdb_points)


def _process_stat(stat_key, stat_time, stat_value, tags, influxdb_points):
    """
    Create InfluxDB points/measurements from the stat query result.
    """
    if type(stat_value) == dict:
        _process_stat_dict(stat_key, stat_time, stat_value, tags,
                influxdb_points)
    elif type(stat_value) == list:
        _process_stat_list(stat_key, stat_time, stat_value, tags,
                influxdb_points)
    else:
        if stat_value == "":
            return # InfluxDB does not like empty string stats
        point = _build_influxdb_point(stat_time, stat_key, stat_value, tags)
        influxdb_points.append(point)


def _build_influxdb_point(unix_ts_secs, measurement, value, tags):
    """
    Build the json for an InfluxDB data point.
    """
    timestamp_ns = unix_ts_secs * 1000000000 # convert to nanoseconds
    point_json = {
            "measurement": measurement,
            "tags": tags,
            "time": timestamp_ns,
            "fields": {"value": value}}
    return point_json


def _get_point_names(points):
    names = ""
    for point in points:
        names += point["measurement"]
        names += " "
    return names


def _write_points(points, num_points):
    """
    Write the points to the InfluxDB in groups that are MAX_POINTS_PER_WRITE in
    size.
    """
    LOG.debug("Writing points %d", num_points)
    write_index = 0
    points_written = 0
    while write_index < num_points:
        try:
            max_write_index = write_index + MAX_POINTS_PER_WRITE
            write_points = points[write_index:max_write_index]
            g_client.write_points(write_points)
            points_written += len(write_points)
        except InfluxDBServerError as svr_exc:
            LOG.error("InfluxDBServerError: %s\nFailed to write points: %s",
                    str(svr_exc), _get_point_names(write_points))
        except InfluxDBClientError as client_exc:
            LOG.error("InfluxDBClientError writing points: %s\n"\
                    "Error: %s", _get_point_names(write_points),
                    str(client_exc))
        write_index += MAX_POINTS_PER_WRITE

    return points_written
