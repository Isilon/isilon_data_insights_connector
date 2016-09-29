from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBServerError, InfluxDBClientError

from ast import literal_eval
import getpass
import logging
import requests.exceptions
import sys


# InfluxDBClient interface
g_client = None
LOG = logging.getLogger(__name__)

# Number of points to queue up before writing it to the database.
MAX_POINTS_PER_WRITE = 100
# separator used to concatenate stat keys with sub-keys derived from stats
# whose value is a dict or list.
SUB_KEY_SEPARATOR = "."


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
    num_points = 0
    points_written = 0
    for stat in stats:
        if stat.devid != 0:
            tags["node"] = stat.devid
        # check if the stat query returned an error
        if stat.error is not None:
            LOG.warning("Query for stat: '%s' on '%s', returned error: '%s'.",
                    str(stat.key), cluster, str(stat.error))
            continue
        # Process stat and then write points if list is large enough. Note
        # that an individual stat might result in multiple points being added
        # to the points depending on the type of the stat's value.
        try:
            # the stat value's data type is variable depending on the key so
            # use literal_eval() to convert it to the correct type
            eval_value = literal_eval(stat.value)
            # convert tuples to a list for simplicity
            if type(eval_value) == tuple:
                stat.value = list(eval_value)
            else:
                stat.value = eval_value
        except: # if literal_eval throws an exception we'll just insert it
            # as string value, which does not really make sense for InfluxDB,
            # but oh well.
            pass
        influxdb_point = \
                _influxdb_point_from_stat(
                        stat.time, tags, stat.key, stat.value)
        if influxdb_point is not None and len(influxdb_point["fields"]) > 0:
            influxdb_points.append(influxdb_point)
            num_points += 1
        if num_points > MAX_POINTS_PER_WRITE:
            points_written += _write_points(influxdb_points, num_points)
            influxdb_points = []
            num_points = 0
    # send left over points to influxdb
    num_points = len(influxdb_points)
    if num_points > 0:
        points_written += _write_points(influxdb_points, num_points)
    LOG.debug("Done processing stats, wrote %d points.", points_written)


def _add_field(fields, field_name, field_value, field_value_type):
    if field_value_type == long or field_value_type == int:
        # convert integers to float because InfluxDB only supports 64 bit
        # signed integers, so doing this prevents an "out of range" error when
        # inserting values that are unsigned 64 bit integers.
        # Note that it is not clear if the PAPI is smart enough to always
        # encode 64 bit unsigned integers as type 'long' even when the actual
        # value is fits into a 64 bit signed integer and because InfluxDB
        # wants a measurement to always be of the same type, the safest thing
        # to do is convert integers to float.
        field_value = float(field_value)
    fields.append((field_name, field_value))


def _process_stat_dict(stat_value, fields, tags, prefix=""):
    """
    Add (field_name, field_value) tuples to the fields list for any
    non-string or non-"id" items in the stat_value dict so that they can be
    used for the "fields" parameter of the InfluxDB point.
    Any string or keys with "id" on the end of their name get turned into tags.
    """
    for key, value in stat_value.iteritems():
        value_type = type(value)
        field_name = prefix + key
        if (value_type == str or value_type == unicode) \
                or (key[-2:] == "id" and value_type == int):
            tags[field_name] = value
        elif value_type == list:
            list_prefix = field_name + SUB_KEY_SEPARATOR
            _process_stat_list(value, fields, tags, list_prefix)
        elif value_type == dict:
            dict_prefix = field_name + SUB_KEY_SEPARATOR
            _process_stat_dict(value, fields, tags, dict_prefix)
        else:
            _add_field(fields, field_name, value, value_type)


def _process_stat_list(stat_value, fields, tags, prefix=""):
    """
    Add (field_name, field_value) tuples to the fields list for any
    non-string or non-"id" items in the stat_value dict so that they can be
    used for the "fields" parameter of the InfluxDB point.
    """
    field_name = prefix + "value"
    for index in range(0, len(stat_value)):
        list_value = stat_value[index]
        value_type = type(list_value)
        if value_type == dict:
            _process_stat_dict(list_value, fields, tags, prefix)
        else:
            item_name = field_name + SUB_KEY_SEPARATOR + str(index)
            if value_type == list:
                # AFAIK there are no instances of a list that contains a list
                # but just in case one is added in the future, deal with it.
                item_name += SUB_KEY_SEPARATOR
                _process_stat_list(list_value, fields, tags, item_name)
            else:
                _add_field(fields, item_name, list_value, value_type)


def _influxdb_point_from_stat(stat_time, tags, stat_key, stat_value):
    """
    Create InfluxDB points/measurements from the stat query result.
    """
    point_tags = tags.copy()
    fields = []
    stat_value_type = type(stat_value)
    if stat_value_type == dict:
        _process_stat_dict(stat_value, fields, point_tags)
    elif stat_value_type == list:
        _process_stat_list(stat_value, fields, point_tags)
    else:
        if stat_value == "":
            return None # InfluxDB does not like empty string stats
        _add_field(fields, "value", stat_value, stat_value_type)
    return _build_influxdb_point(stat_time, point_tags, stat_key, fields)


def _build_influxdb_point(unix_ts_secs, tags, measurement, fields):
    """
    Build the json for an InfluxDB data point.
    """
    timestamp_ns = unix_ts_secs * 1000000000 # convert to nanoseconds
    point_json = {
            "measurement": measurement,
            "tags": tags,
            "time": timestamp_ns,
            "fields": {}}

    for field_name, field_value in fields:
        point_json["fields"][field_name] = field_value

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
        max_write_index = write_index + MAX_POINTS_PER_WRITE
        write_points = points[write_index:max_write_index]
        try:
            g_client.write_points(write_points)
            points_written += len(write_points)
        except InfluxDBServerError as svr_exc:
            LOG.error("InfluxDBServerError: %s\nFailed to write points: %s",
                    str(svr_exc), _get_point_names(write_points))
        except InfluxDBClientError as client_exc:
            LOG.error("InfluxDBClientError writing points: %s\n"\
                    "Error: %s", _get_point_names(write_points),
                    str(client_exc))
        except requests.exceptions.ConnectionError as req_exc:
            LOG.error("ConnectionError exception caught writing points: %s\n"\
                    "Error: %s", _get_point_names(write_points),
                    str(req_exc))
        write_index += MAX_POINTS_PER_WRITE

    return points_written
