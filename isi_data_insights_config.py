"""
This file contains utility functions for configuring the IsiDataInsightsDaemon
via command line args and config file.
"""
import argparse
import ConfigParser
import getpass
import logging
import os
import re
import sys
import urllib3

from ast import literal_eval
from Equation import Expression

from isi_data_insights_daemon import StatsConfig, ClusterConfig, \
        ClusterCompositeStatComputer, EquationStatComputer, \
        PercentChangeStatComputer, DerivedStatInput
from isi_stats_client import IsiStatsClient
import isi_sdk_utils


LOG = logging.getLogger(__name__)

DEFAULT_PID_FILE = "./isi_data_insights_d.pid"
DEFAULT_LOG_FILE = "./isi_data_insights_d.log"
DEFAULT_LOG_LEVEL = "INFO"
# name of the section in the config file where the main/global settings for the
# daemon are stored.
MAIN_CFG_SEC = "isi_data_insights_d"
# the number of seconds to wait between updates for stats that are
# continually kept up-to-date.
ONE_SEC = 1 # seconds
# the default minimum update interval (even if a particular stat key is updated
# at a higher rate than this we will still only query at this rate in order to
# prevent the cluster from being overloaded with stat queries).
MIN_UPDATE_INTERVAL = 30 # seconds
# name of the config file param that can be used to specify a lower
# MIN_UPDATE_INTERVAL.
MIN_UPDATE_INTERVAL_OVERRIDE_PARAM = "min_update_interval_override"

def avg(stat_values):
    return sum(stat_values) / len(stat_values)

# operations use by ClusterCompositeStatComputer
COMPOSITE_OPERATIONS = {
    "avg": avg,
    "max": max,
    "min": min,
    "sum": sum
}

# keep track of auth data that we have username and passwords for so that we
# don't prompt more than once.
g_cluster_auth_data = {}
# keep track of the name and version of each cluster
g_cluster_configs = {}


def _add_cluster_auth_data(cluster_address, username, password, verify_ssl):
    # update cluster auth data
    g_cluster_auth_data[cluster_address] = (username, password, verify_ssl)


def _process_config_file_clusters(clusters):
    cluster_list = []
    cluster_configs = clusters.split()
    for cluster_config in cluster_configs:
        # expected [username:password@]address[:bool]
        at_split = cluster_config.split("@")
        if len(at_split) == 2:
            user_pass_split = at_split[0].split(":", 1)
            if len(user_pass_split) != 2:
                print >> sys.stderr, "Config file contains invalid cluster "\
                        "config: %s in %s (expected <username>:<password> "\
                        "prefix)." % (cluster_config, clusters)
                sys.exit(1)
            username = user_pass_split[0]
            password = user_pass_split[1]
            # If they provide a username and password then verify_ssl defaults
            # to false. Otherwise, unless they explicity provide it in the
            # config, we will prompt them for that parameter when we prompt for
            # the username and password.
            verify_ssl = False
        else:
            username = None
            password = None
            verify_ssl = None
        verify_ssl_split = at_split[-1].split(":", 1)
        if len(verify_ssl_split) == 1:
            cluster_address = verify_ssl_split[0]
        else:
            try:
                # try to convert to a bool
                verify_ssl = literal_eval(verify_ssl_split[-1])
                if type(verify_ssl) != bool:
                    raise Exception
            except Exception:
                print >> sys.stderr, "Config file contains invalid cluster "\
                        "config: %s (expected True or False on end)" \
                        % (cluster_config)
                sys.exit(1)
            cluster_address = verify_ssl_split[0]
        # add to cache of known cluster auth usernames and passwords
        _add_cluster_auth_data(cluster_address, username, password, verify_ssl)
        cluster_list.append(cluster_address)

    return cluster_list


def _get_cluster_auth_data(cluster):
    try:
        username = password = verify_ssl = None
        # check if we already know the username and password
        username, password, verify_ssl = g_cluster_auth_data[cluster]
        if username is None or password is None or verify_ssl is None:
            # this happens when some of the auth params were provided in the
            # config file or cli, but not all.
            raise KeyError
    except KeyError:
        # get username and password for input clusters
        if username is None:
            username = raw_input("Please provide the username used to access "\
                    + cluster + " via PAPI: ")
        if password is None:
            password = getpass.getpass("Password: ")
        while verify_ssl is None:
            verify_ssl_resp = raw_input("Verify SSL cert [y/n]: ")
            if verify_ssl_resp == "yes" or verify_ssl_resp == "y":
                verify_ssl = True
            elif verify_ssl_resp == "no" or verify_ssl_resp == "n":
                verify_ssl = False
        # add to cache of known cluster auth usernames and passwords
        _add_cluster_auth_data(cluster, username, password, verify_ssl)

    return username, password, verify_ssl


def _query_cluster_name(cluster_address, isi_sdk, api_client):
    # get the Cluster API
    cluster_api = isi_sdk.ClusterApi(api_client)
    try:
        resp = cluster_api.get_cluster_identity()
        return resp.name
    except isi_sdk.rest.ApiException:
        # if get_cluster_identity() doesn't work just use the address
        return cluster_address


def _build_cluster_configs(cluster_list):
    cluster_configs = []
    for cluster in cluster_list:
        username, password, verify_ssl = _get_cluster_auth_data(cluster)

        if cluster in g_cluster_configs:
            cluster_name, isi_sdk, api_client, version = \
                    g_cluster_configs[cluster]
        else:
            if verify_ssl is False:
                urllib3.disable_warnings()
            try:
                isi_sdk, api_client, version = \
                        isi_sdk_utils.configure(
                                cluster, username, password, verify_ssl)
            except RuntimeError as exc:
                print >> sys.stderr, "Failed to configure SDK for " \
                        "cluster %s. Exception raised: %s" \
                        % (cluster, str(exc))
                sys.exit(1)
            print "Configured %s as version %d cluster, using SDK %s." \
                    % (cluster, int(version), isi_sdk.__name__)
            cluster_name = \
                    _query_cluster_name(cluster, isi_sdk, api_client)
            g_cluster_configs[cluster] = \
                    cluster_name, isi_sdk, api_client, version

        cluster_config = \
                ClusterConfig(
                        cluster, cluster_name, version, isi_sdk, api_client)
        cluster_configs.append(cluster_config)

    return cluster_configs


def _configure_stat_group(daemon,
        update_interval, cluster_configs, stats_list,
        cluster_composite_stats=None,
        equation_stats=None,
        pct_change_stats=None,
        final_equation_stats=None):
    """
    Configure the daemon with some StatsConfigs.
    """
    # configure daemon with stats
    if update_interval < MIN_UPDATE_INTERVAL:
        LOG.warning("The following stats are set to be queried at a faster "\
                "rate, %d seconds, than the MIN_UPDATE_INTERVAL of %d "\
                "seconds. To configure a shorter MIN_UPDATE_INTERVAL specify "\
                "it with the %s param in the %s section of the config file. "\
                "Stats:\n\t%s", update_interval, MIN_UPDATE_INTERVAL,
                    MIN_UPDATE_INTERVAL_OVERRIDE_PARAM, MAIN_CFG_SEC,
                    str(stats_list))
        update_interval = MIN_UPDATE_INTERVAL
    stats_config = \
        StatsConfig(cluster_configs, stats_list, update_interval)
    if cluster_composite_stats is not None:
        stats_config.cluster_composite_stats.extend(cluster_composite_stats)
    if equation_stats is not None:
        stats_config.equation_stats.extend(equation_stats)
    if pct_change_stats is not None:
        stats_config.pct_change_stats.extend(pct_change_stats)
    if final_equation_stats is not None:
        stats_config.final_equation_stats.extend(final_equation_stats)
    daemon.add_stats(stats_config)


def _query_stats_metadata(cluster, stat_names):
    """
    Query the specified cluster for the metadata of the stats specified in
    stat_names list.
    """
    stats_api = cluster.isi_sdk.StatisticsApi(cluster.api_client)
    isi_stats_client = IsiStatsClient(stats_api)
    return isi_stats_client.get_stats_metadata(stat_names)


def _compute_stat_group_update_intervals(
        update_interval_multiplier,
        cluster_configs,
        stat_names,
        update_intervals):
    # update interval is supposed to be set relative to the collection
    # interval, which might be different for each stat and each cluster.
    for cluster in cluster_configs:
        stats_metadata = _query_stats_metadata(cluster, stat_names)
        for stat_index in range(0, len(stats_metadata)):
            stat_metadata = stats_metadata[stat_index]
            stat_name = stat_names[stat_index]
            # cache time is the length of time the system will store the
            # value before it updates.
            cache_time = -1
            if stat_metadata.default_cache_time:
                cache_time = \
                        ((stat_metadata.default_cache_time + 1)
                        # add one to the default_cache_time because the new
                        # value is not set until 1 second after the cache time.
                                * update_interval_multiplier)
            # the policy intervals seem to override the default cache time
            if stat_metadata.policies:
                smallest_interval = cache_time
                for policy in stat_metadata.policies:
                    if smallest_interval == -1:
                        smallest_interval = policy.interval
                    else:
                        smallest_interval = \
                            min(policy.interval,
                                    smallest_interval)
                cache_time = \
                        (smallest_interval * update_interval_multiplier)
            # if the cache_time is still -1 then it means that the statistic is
            # continually updated, so the fastest it can be queried is
            # once every second.
            if cache_time == -1:
                cache_time = ONE_SEC * update_interval_multiplier
            try:
                update_interval = update_intervals[cache_time]
                update_interval[0].add(cluster)
                update_interval[1].add(stat_name)
            except KeyError:
                # insert a new interval time
                update_intervals[cache_time] = \
                        (set([cluster]), set([stat_name]))


def _configure_stat_groups_via_file(daemon,
        config_file, stat_group, global_cluster_list):
    cluster_list = []
    cluster_list.extend(global_cluster_list)
    try:
        # process clusters specific to this stat group (if any)
        clusters_param = config_file.get(stat_group, "clusters")
        stat_group_clusters = _process_config_file_clusters(clusters_param)
        cluster_list.extend(stat_group_clusters)
        # remove duplicates
        cluster_list = list(set(cluster_list))
    except ConfigParser.NoOptionError:
        pass

    if len(cluster_list) == 0:
        print >> sys.stderr, "The %s stat group has no clusters to query."\
                % (stat_group)
        print >> sys.stderr, "You must provide either a global list of " \
                "clusters to query for all stat groups, or a per-stat-" \
                "group list of clusters, or both."
        sys.exit(1)

    cluster_configs = _build_cluster_configs(cluster_list)

    update_interval_param = config_file.get(stat_group, "update_interval")
    stat_names = config_file.get(stat_group, "stats").split()
    # remove duplicates
    stat_names = list(set(stat_names))
    # deal with derived stats (if any)
    composite_stats = []
    if config_file.has_option(stat_group, "composite_stats") is True:
        composite_stats = \
                _parse_derived_stats(config_file,
                        stat_group, "composite_stats",
                        _parse_composite_stats)

    eq_stats = []
    if config_file.has_option(stat_group, "equation_stats") is True:
        eq_stats = \
                _build_equation_stats_list(
                        config_file, stat_group, "equation_stats")

    pct_change_stats = []
    if config_file.has_option(stat_group, "percent_change_stats") is True:
        pct_change_stats = \
                _parse_derived_stats(config_file,
                        stat_group, "percent_change_stats",
                        _parse_pct_change_stats)

    final_eq_stats = []
    if config_file.has_option(stat_group, "final_equation_stats") is True:
        final_eq_stats = \
                _build_equation_stats_list(
                        config_file, stat_group, "final_equation_stats")

    update_intervals = {}
    if update_interval_param.startswith("*"):
        try:
            update_interval_multiplier = 1 if update_interval_param == "*" \
                    else int(update_interval_param[1:])
        except ValueError as exc:
            print >> sys.stderr, "Failed to parse update interval multiplier "\
                    "from %s stat group.\nERROR: %s" % (stat_group, str(exc))
            sys.exit(1)
        print "Computing update intervals for stat group: %s." % stat_group
        _compute_stat_group_update_intervals(
                update_interval_multiplier,
                cluster_configs,
                stat_names,
                update_intervals)
    else:
        try:
            update_interval = int(update_interval_param)
        except ValueError as exc:
            print >> sys.stderr, "Failed to parse update interval from %s "\
                    "stat group.\nERROR: %s" % (stat_group, str(exc))
            sys.exit(1)
        update_intervals[update_interval] = \
                (cluster_configs, stat_names)

    # TODO - fix this - for now if there are derived stats then we are going to
    # query all the stats in this section at once (i.e. using the the smallest
    # of the configured update intervals) in order to make sure that all of the
    # input parameters of the derived stats are available at once.
    if len(composite_stats) > 0 \
            or len(eq_stats) > 0 \
            or len(pct_change_stats) > 0 \
            or len(final_eq_stats) > 0:
        update_interval_keys = update_intervals.keys()
        update_interval_keys.sort()
        update_interval = update_interval_keys[0]
        _configure_stat_group(daemon,
                update_interval,
                cluster_configs, stat_names,
                composite_stats, eq_stats, pct_change_stats, final_eq_stats)
    else:
        for update_interval, clusters_stats_tuple \
                in update_intervals.iteritems():
            # first item in clusters_stats_tuple is the unique list of clusters
            # associated with the current update_interval, the second item is the
            # unique list of stats to query on the set of clusters at the current
            # update_interval.
            _configure_stat_group(daemon,
                    update_interval,
                    clusters_stats_tuple[0],
                    clusters_stats_tuple[1])


def _parse_derived_stats(config_file, stat_group, derived_stats_name, parse_func):
    derived_stats_cfg = config_file.get(stat_group, derived_stats_name)
    try:
        derived_stats = \
                parse_func(derived_stats_cfg)
    except RuntimeError as rterr:
        print >> sys.stderr, "Failed to parse %s from %s " \
                "section. %s" % (derived_stats_name, stat_group, str(rterr))
        sys.exit(1)

    return derived_stats


def _parse_fields(in_stat_name):
    split_name = in_stat_name.split(":")
    if len(split_name) == 1:
        return in_stat_name, None

    return split_name[0], tuple(split_name[1:])


def _parse_composite_stats(composite_stats_cfg):
    # Example of what is expected for each stat_cfg:
    # sum(node.ifs.ops.in[:field1:field2])
    composite_stats = []
    for stat_cfg in composite_stats_cfg.split():
        bracket1 = stat_cfg.find('(')
        bracket2 = stat_cfg.find(')')
        if bracket1 <= 0 or bracket2 == -1 \
                or bracket1 > bracket2:
            raise RuntimeError("Failed to parse operation from %s." \
                    "Expected: op(stat) where op is avg, min, max, " \
                    " or sum and stat is the name of a base OneFS " \
                    " statistic name that starts with \"node.\"." \
                    % stat_cfg)
        op_name = stat_cfg[0:bracket1]
        if op_name not in COMPOSITE_OPERATIONS:
            raise RuntimeError("Invalid operation %s specified for %s." \
                    % (op_name, stat_cfg))

        in_stat_name = stat_cfg[bracket1+1:bracket2]
        if in_stat_name.startswith("node.") is False:
            raise RuntimeError("Invalid stat name %s specified for %s." \
                    " Composite stats must start with \"node.\"." \
                    % (op_name, stat_cfg))
        out_stat_name = "cluster.%s.%s" \
                % (in_stat_name.replace(':', '.'), op_name)
        in_stat_name, fields = _parse_fields(in_stat_name)
        # TODO should validate that this is a valid stat name
        composite_stat = \
                ClusterCompositeStatComputer(
                    DerivedStatInput(in_stat_name, fields), out_stat_name,
                    COMPOSITE_OPERATIONS[op_name])
        composite_stats.append(composite_stat)

    return composite_stats


def _build_equation_stats_list(config_file, stat_group, equation_stats):
    eq_stats = []
    eq_stats_list = config_file.get(stat_group, equation_stats).split()
    for eq_stat in eq_stats_list:
        eq_stat_names = \
                _parse_derived_stats(config_file,
                        stat_group, eq_stat,
                        _parse_equation_stats)
        cfg_expression = config_file.get(stat_group, eq_stat)
        # the Equation package doesn't like having '.' characters in the
        # input param names, so we have to replace them with placeholder
        # names.
        eq_func = \
                _build_equation_expression(cfg_expression, eq_stat_names)
        eq_stat_inputs = _build_equation_stat_inputs(eq_stat_names)
        eq_stats.append(EquationStatComputer(eq_func, eq_stat_inputs, eq_stat))

    return eq_stats


def _build_equation_stat_inputs(eq_stat_names):
    input_stats = []
    for stat_name in eq_stat_names:
        stat_name, fields = _parse_fields(stat_name)
        input_stats.append(DerivedStatInput(stat_name, fields))

    return input_stats


def _parse_equation_stats(equation_stat_expression):
    # Example of what is expected:
    # (cluster.node.ifs.ops.in.sum + cluster.node.ifs.ops.out.sum)
    # * cluster.node.disk.iosched.latency.avg.avg
    # Example of what is expected from stat with specific fields:
    # (cluster.protostats.nfs.total:op_count
    #  + cluster.protostats.smb2.total:op_count)
    equation_stats = re.findall("[a-zA-Z.:_0-9]+", equation_stat_expression)

    # remove items that don't start with an alphabet character
    equation_stats = [eq_stat for eq_stat in equation_stats \
            if eq_stat[0].isalpha()]
    return equation_stats


def _build_equation_expression(cfg_expression, eq_stat_names):
    params_list = []
    for eindex in range(0, len(eq_stat_names)):
        eq_stat_name = eq_stat_names[eindex]
        param_name = "param" + str(eindex)
        cfg_expression = cfg_expression.replace(eq_stat_name, param_name, 1)
        params_list.append(param_name)

    return Expression(cfg_expression, params_list)


def _parse_pct_change_stats(pct_change_stats_cfg):
    # Expected is just a white-space delimitted list of stat names
    pct_change_stats = []
    for stat_name in pct_change_stats_cfg.split():
        out_stat_name = stat_name.replace(':', '.') + ".percentchange"
        stat_name, fields = _parse_fields(stat_name)
        pct_change_stats.append(
            PercentChangeStatComputer(
                DerivedStatInput(stat_name, fields), out_stat_name))
    return pct_change_stats


def _configure_stat_groups_via_cli(daemon, args):
    if len(args.stat_groups) == 0:
        print >> sys.stderr, "You must provide a set of stats to query via " \
            "the --stats command line argument or a configuration file."
        sys.exit(1)

    if not args.update_intervals:
        # for some reason if i try to use default=[MIN_UPDATE_INTERVAL] in the
        # argparser for the update_intervals arg then my list always has a
        # MIN_UPDATE_INTERVAL in addition to any intervals actually provided by
        # the user on the command line, so i need to setup the default here
        args.update_intervals.append(MIN_UPDATE_INTERVAL)

    if len(args.stat_groups) != len(args.update_intervals):
        print >> sys.stderr, "The number of update intervals must be the "\
                + "same as the number of stat groups."
        sys.exit(1)

    cluster_list = args.clusters.split(",")
    # if args.clusters is the empty string then 1st element will be empty
    if cluster_list[0] == "":
        print >> sys.stderr, "Please provide at least one input cluster."
        sys.exit(1)

    # remove duplicates
    cluster_list = list(set(cluster_list))
    cluster_configs = _build_cluster_configs(cluster_list)

    for index in range(0, len(args.stat_groups)):
        stats_list = args.stat_groups[index].split(",")
        # split always results in at least one item, so check if the first
        # item is empty to validate the stats input arg
        if stats_list[0] == "":
            print >> sys.stderr, "Please provide at least one stat name."
            sys.exit(1)
        update_interval = args.update_intervals[index]
        _configure_stat_group(daemon,
                update_interval, cluster_configs, stats_list)


def _configure_stats_processor(daemon, stats_processor, processor_args):
    try:
        processor = __import__(stats_processor, fromlist=[''])
    except ImportError:
        print >> sys.stderr, "Unable to load stats processor: %s." \
                % stats_processor
        sys.exit(1)

    try:
        arg_list = processor_args.split(" ") \
                if processor_args != "" else []
        daemon.set_stats_processor(processor, arg_list)
    except AttributeError as exception:
        print >> sys.stderr, "Failed to configure %s as stats processor. %s" \
                % (stats_processor, str(exception))
        sys.exit(1)


def _log_level_str_to_enum(log_level):
    if log_level.upper() == "DEBUG":
        return logging.DEBUG
    elif log_level.upper() == "INFO":
        return logging.INFO
    elif log_level.upper() == "WARNING":
        return logging.WARNING
    elif log_level.upper() == "ERROR":
        return logging.ERROR
    elif log_level.upper() == "CRITICAL":
        return logging.CRITICAL
    else:
        print "Invalid logging level: " + log_level + ", setting to INFO."
        return logging.INFO


def _update_args_with_config_file(config_file, args):
    # command line args override config file params
    if args.pid_file is None \
            and config_file.has_option(MAIN_CFG_SEC, "pid_file"):
        args.pid_file = config_file.get(MAIN_CFG_SEC, "pid_file")
    if args.log_file is None \
            and config_file.has_option(MAIN_CFG_SEC, "log_file"):
        args.log_file = config_file.get(MAIN_CFG_SEC, "log_file")
    if args.log_level is None \
            and config_file.has_option(MAIN_CFG_SEC, "log_level"):
        args.log_level = config_file.get(MAIN_CFG_SEC, "log_level")


def _print_stat_groups(daemon):
    """
    Print out the list of stat sets that were configured for the daemon prior
    to starting it so that user can verify that it was configured as expected.
    """
    for update_interval, stat_set in daemon.get_next_stat_set():
        msg = "Configured stat set:\n\tClusters: %s\n\t"\
                "Update Interval: %d\n\tStat Keys: %s" \
                % (str(stat_set.cluster_configs), update_interval,
                        str(stat_set.stats))
        # print it to stdout and the log file.
        print msg
        LOG.debug(msg)


def configure_via_file(daemon, args, config_file):
    """
    Configure the daemon's stat groups and the stats processor via command line
    arguments and configuration file. The command line args override settings
    provided in the config file.
    """
    # Command line args override config file params
    if not args.stats_processor \
            and config_file.has_option(MAIN_CFG_SEC, "stats_processor") is True:
        args.stats_processor = config_file.get(MAIN_CFG_SEC, "stats_processor")
    if not args.processor_args \
            and config_file.has_option(
                    MAIN_CFG_SEC, "stats_processor_args") is True:
        args.processor_args = \
                config_file.get(MAIN_CFG_SEC, "stats_processor_args")
    _configure_stats_processor(daemon,
            args.stats_processor, args.processor_args)

    # check if the MAIN_CFG_SEC has the MIN_UPDATE_INTERVAL_OVERRIDE_PARAM
    if config_file.has_option(MAIN_CFG_SEC,
            MIN_UPDATE_INTERVAL_OVERRIDE_PARAM):
        global MIN_UPDATE_INTERVAL
        try:
            override_update_interval = int(
                    config_file.get(MAIN_CFG_SEC,
                        MIN_UPDATE_INTERVAL_OVERRIDE_PARAM))
        except ValueError as exc:
            print >> sys.stderr, "Failed to parse %s from %s "\
                    "section.\nERROR: %s" % (
                            MIN_UPDATE_INTERVAL_OVERRIDE_PARAM,
                            MAIN_CFG_SEC, str(exc))
            sys.exit(1)

        LOG.warning("Overriding MIN_UPDATE_INTERVAL of %d seconds with "\
                "%d seconds.", MIN_UPDATE_INTERVAL, override_update_interval)
        MIN_UPDATE_INTERVAL = override_update_interval

    # if there are any clusters, stats, or update_intervals specified via CLI
    # then try to configure the daemon using them first.
    if args.update_intervals or args.stat_groups or args.clusters:
        _configure_stat_groups_via_cli(daemon, args)
    global_cluster_list = []
    if args.clusters:
        global_cluster_list = args.clusters.split(",")
    elif config_file.has_option(MAIN_CFG_SEC, "clusters"):
        global_cluster_list = \
                _process_config_file_clusters(config_file.get(
                    MAIN_CFG_SEC, "clusters"))
    # remove duplicates
    global_cluster_list = list(set(global_cluster_list))

    # now configure with config file params too
    if config_file.has_option(MAIN_CFG_SEC, "active_stat_groups"):
        active_stat_groups = config_file.get(MAIN_CFG_SEC,
                "active_stat_groups").split()
        for stat_group in active_stat_groups:
            _configure_stat_groups_via_file(daemon,
                    config_file, stat_group, global_cluster_list)

    # check that at least one stat group was added to the daemon.
    if daemon.get_stat_set_count() == 0:
        print >> sys.stderr, "Please provide stat groups to query via "\
                "command line args or via config file parameters."
        sys.exit(1)

    _print_stat_groups(daemon)


def configure_via_cli(daemon, args):
    """
    Configure the daemon's stat groups and the stats processor via command line
    arguments.
    """
    _configure_stat_groups_via_cli(daemon, args)
    _configure_stats_processor(daemon,
            args.stats_processor, args.processor_args)

    _print_stat_groups(daemon)


def configure_logging_via_cli(args):
    """
    Setup the logging from command line args.
    """
    if args.action != "debug":
        if args.log_file is None:
            args.log_file = DEFAULT_LOG_FILE

        parent_dir = os.path.dirname(args.log_file)
        if parent_dir \
                and os.path.exists(parent_dir) is False:
            print >> sys.stderr, "Invalid log file path: %s." \
                    % (args.log_file)
            sys.exit(1)

        if args.log_level is None:
            args.log_level = DEFAULT_LOG_LEVEL

        log_level = _log_level_str_to_enum(args.log_level)
        logging.basicConfig(filename=args.log_file, level=log_level,
                format='%(asctime)s:%(name)s:%(levelname)s: %(message)s')
    else: # configure logging to stdout for 'debug' action
        logging.basicConfig(stream=sys.stdout, level=logging.DEBUG,
                format='%(asctime)s:%(name)s:%(levelname)s: %(message)s')


def configure_args_via_file(args):
    """
    Load the config_file, if there is one, then check if the pid_file,
    log_file, and log_level parameters are provided in the config file. If they
    are and they are not set via CLI args then use the config file to set them.
    """
    config_file = None
    if args.config_file is not None:
        try:
            config_file = ConfigParser.ConfigParser()
            with open(args.config_file, "r") as cfg_fp:
                config_file.readfp(cfg_fp)
        except Exception as exc:
            print >> sys.stderr, "Failed to parse config file: %s.\n"\
                    "ERROR:\n%s." % (args.config_file, str(exc))
            sys.exit(1)
        _update_args_with_config_file(config_file, args)
    return config_file


def process_pid_file_arg(pid_file, action):
    """
    Make sure the pid_file argument is a valid path. Set it to the default if
    it was not specified.
    """
    if pid_file is None:
        pid_file = DEFAULT_PID_FILE

    parent_dir = os.path.dirname(pid_file)
    if parent_dir \
            and os.path.exists(parent_dir) is False:
        print >> sys.stderr, "Invalid pid file path: %s." % pid_file
        sys.exit(1)

    pid_file_path = os.path.abspath(pid_file)
    if (action == "stop" or action == "restart") \
            and os.path.exists(pid_file_path) is False:
        print >> sys.stderr, "Invalid pid file path: %s." % pid_file
        sys.exit(1)

    return pid_file_path



def parse_cli():
    """
    Setup the command line args and parse them.
    """
    argparser = argparse.ArgumentParser(
            description='Starts, stops, or restarts the '\
                    'isi_data_insights_daemon.')
    argparser.add_argument('action', help="Specifies to 'start', 'stop', "
            "'restart', or 'debug' the daemon.")
    argparser.add_argument('-c', '--config-file', dest='config_file',
            help="Set the path to the config file. The default value is "
            "'./isi_data_insights_d.cfg'.",
            action='store', default="./isi_data_insights_d.cfg")
    argparser.add_argument('-a', '--processor-args', dest='processor_args',
            help="Specifies the args to pass to the start function of the "
            "results processor's start function.",
            action="store", default="")
    argparser.add_argument('-l', '--log-file', dest='log_file',
            help="Set the path to the log file. The default value is "
            "'./isi_data_insights_d.log'.",
            action='store', default=None)
    argparser.add_argument('-e', '--log-level', dest='log_level',
            help="Set the logging level (debug, info, warning, error, or "
            "critical).", action='store', default=None)
    argparser.add_argument('-p', '--pid-file', dest='pid_file',
            help="Set the path to the daemon pid file. The default value is "
            "'./isi_data_insights_d.pid'.",
            action='store', default=None)
    argparser.add_argument('-x', '--stats-processor', dest='stats_processor',
            help="Name of the Python module used to process stats query "
            "results. The specified Python module must define "
            "a function named process(results_list) where results_list is a"
            "list of isi_sdk.models.statistics_current_stat objects."
            "StatisticsCurrentStat objects.  The module may also optionally "
            "define start(args) and stop() functions. Use the "
            "--processor-args to specify args to pass to the results "
            "processor's start function.",
            action='store', default=None)
    argparser.add_argument('-i', '--input-clusters', dest='clusters',
            help="Comma delimitted list of clusters to monitor (either "
            "hostnames or ip-addresses)",
            action='store', default="")
    argparser.add_argument('-s', '--stats', dest='stat_groups',
            help="Comma delimitted list of stat names to monitor. Accepts"
            "multiple.", default=[], action='append')
    argparser.add_argument('-u', '--update-interval', dest='update_intervals',
            help="Specifies how often, in seconds, the input clusters should "
            "be polled for each stat group. Accepts multiple.",
            action='append', default=[], type=int)

    return argparser.parse_args()
