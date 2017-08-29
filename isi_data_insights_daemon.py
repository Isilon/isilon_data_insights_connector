import gevent
import gevent.pool

from daemons.prefab import run
from ast import literal_eval
import logging
import sys
import time
import urllib3.exceptions

from isi_stats_client import IsiStatsClient

MAX_ASYNC_QUERIES = 20

LOG = logging.getLogger(__name__)


class ClusterConfig(object):
    def __init__(self,
            address, name, version, isi_sdk, api_client):
        self.address = address
        self.name = name
        self.version = version
        self.isi_sdk = isi_sdk
        self.api_client = api_client


    def __eq__(self, other):
        """
        Override __eq__ so that we can store this in a list and check for its
        existence.
        """
        return self.address == other.address


    def __hash__(self):
        """
        Override __hash__ so that we can store this in a dict.
        """
        return hash(str(self))


    def __repr__(self):
        return self.name


class DerivedStatsProcessor(object):
    def __init__(self, derived_stat_computers):
        self._derived_stat_computers = derived_stat_computers


    def begin_process(self, cluster_name):
        for derived_stat_computer in self._derived_stat_computers:
            derived_stat_computer.begin_process(cluster_name)


    def select_stat(self, stat):
        for derived_stat_computer in self._derived_stat_computers:
            derived_stat_computer.select_stat(stat)


    def end_process(self, cluster_name):
        for derived_stat_computer in self._derived_stat_computers:
            derived_stat_computer.end_process(cluster_name)


    def stats(self):
        for derived_stat_computer in self._derived_stat_computers:
            yield derived_stat_computer


class DerivedStatComputer(object):

    def __init__(self, out_stat_name):
        self._initialize()
        self.out_stat_name = out_stat_name

    def _initialize(self):
        self._selected_stat_timestamps = {}
        self._selected_stat_errors = {}


    def begin_process(self, cluster_name):
        self._initialize()


    def end_process(self, cluster_name):
        pass


    def process(self, stat):
        pass


    def _choose_stat(self, stat):
        LOG.debug("Choose stat: %s", stat.key)
        try:
            self._selected_stat_timestamps[stat.devid].append(long(stat.time))
        except KeyError:
            self._selected_stat_timestamps[stat.devid] = [long(stat.time)]


    def _create_derived_stat(self, value, devid=0, error=None):
        class DerivedStat(object):
            """ Pretend to be a Stat returned by PAPI """
            def __init__(self, key, val, node, timestamp, err):
                self.key = key
                self.value = val
                self.devid = node
                self.time = timestamp
                self.error = err
                self.error_code = \
                        None if error is None else 1

        avg_timestamp = 0
        if error is not None:
            try:
                avg_timestamp = self._get_timestamp_avg(devid)
            except ZeroDivisionError:
                error = "Caught ZeroDivisionError from _get_timestamp_avg " \
                        "for stat %s on node %s." \
                        % (self.out_stat_name, str(devid))

        return DerivedStat(
                self.out_stat_name, value, devid,
                avg_timestamp, error)


    def _get_timestamp_avg(self, devid):
        if devid not in self._selected_stat_timestamps and devid == 0:
            tot = 0
            tot_count = 0
            for node in self._selected_stat_timestamps:
                tot += sum(self._selected_stat_timestamps[node])
                tot_count += len(self._selected_stat_timestamps[node])
            return long(tot / tot_count)
        return long(sum(self._selected_stat_timestamps[devid])
                / len(self._selected_stat_timestamps[devid]))


class DerivedStatInput(object):
    def __init__(self, stat_name, stat_fields=()):
        self.name = stat_name
        if stat_fields and len(stat_fields) > 0:
            self._stat_fields = stat_fields
        else:
            self._stat_fields = None


    def _lookup(self, stat_value, field, *fields):
        if fields:
            # if stat_value is not a dict or list then this will raise
            # exception, which is what we want it to do.
            if type(stat_value) == dict:
                return self._lookup(stat_value.get(field, {}), *fields)
            else:
                return self._lookup(stat_value[field], *fields)
        return stat_value.get(field)


    def get_value(self, stat_value):
        if self._stat_fields is not None:
            # PAPI has a weird habit of putting stats that have only 1 value
            # into a list. When that happens we just ignore the list
            if type(stat_value) == list:
                num_items = len(stat_value)
                if num_items == 1:
                    stat_value = stat_value[0]
                elif num_items == 0:
                    return None
            return self._lookup(stat_value, *self._stat_fields)
        return stat_value


    @property
    def full_name(self):
        return self._get_full_name(self.name)


    def _get_full_name(self, stat_name):
        if self._stat_fields is not None:
            full_name = stat_name
            full_name += ":"
            full_name += ":".join(self._stat_fields)
        else:
            full_name = stat_name
        return full_name


class ClusterCompositeStatComputer(DerivedStatComputer):
    def __init__(self, input_stat, out_stat_name, operation):
        super(ClusterCompositeStatComputer, self).__init__(out_stat_name)
        self._input_stat = input_stat
        self._operation = operation


    def _initialize(self):
        super(ClusterCompositeStatComputer, self)._initialize()
        self._selected_stat_values = []


    def select_stat(self, stat):
        if stat.key == self._input_stat.name:
            self._selected_stat_values.append(
                    self._input_stat.get_value(stat.value))
            self._choose_stat(stat)


    def compute_derived_stat(self):
        LOG.debug("CCSC %s(%s)",
                str(self._operation.__name__), str(self._selected_stat_values))
        return self._create_derived_stat(
                self._operation(self._selected_stat_values))


class EquationStatComputer(DerivedStatComputer):
    def __init__(self, eq_func, input_stats, out_stat_name):
        super(EquationStatComputer, self).__init__(out_stat_name)
        self._eq_func = eq_func
        self._num_func_args = len(input_stats)
        self._input_stats = input_stats
        self._input_stats_names = {}
        self._input_stat_locations = {}
        for index in range(0, self._num_func_args):
            input_stat = self._input_stats[index]
            # setup mapping from base stat name to input_stat
            try:
                # there might be multiple fields from a single stat with this
                # name so we need to keep a list of input_stats
                self._input_stats_names[input_stat.name].append(input_stat)
            except KeyError:
                self._input_stats_names[input_stat.name] = [input_stat]
            # setup mapping from name to location(s) in the equation
            try:
                self._input_stat_locations[input_stat.full_name].append(index)
            except KeyError:
                self._input_stat_locations[input_stat.full_name] = [index]


    def _initialize(self):
        super(EquationStatComputer, self)._initialize()
        self._selected_stat_values = {}
        self._nodes = set()


    def select_stat(self, stat):
        # check if this stat is included in this equation
        try:
            input_stats = self._input_stats_names[stat.key]
            # if there is an entry for this stat then it is part of my equation
            self._choose_stat(stat)
            self._nodes.add(stat.devid)
        except KeyError:
            return
        for input_stat in input_stats:
            try:
                selected_stats_by_node = \
                        self._selected_stat_values[input_stat.full_name]
            except KeyError:
                self._selected_stat_values[input_stat.full_name] = {}
                selected_stats_by_node = \
                        self._selected_stat_values[input_stat.full_name]

            try:
                selected_stats_by_node[stat.devid] = \
                        input_stat.get_value(stat.value)
            except KeyError:
                selected_stats_by_node = {}
                selected_stats_by_node[stat.devid] = \
                        input_stat.get_value(stat.value)


    def compute_derived_stats(self):
        # return one derived stat per node that the selected stats were
        # collected for.
        derived_stats = []
        for node in self._nodes:
            # for each node build a tuple of the args to the equation
            # by iterating through the intput stat names
            func_args = [None] * self._num_func_args
            for in_stat_name in self._input_stat_locations.keys():
                stat_node = node
                if in_stat_name.startswith("cluster.") is True:
                    stat_node = 0 # this is a cluster stat
                stat_value = self._get_stat_value(in_stat_name, stat_node)
                in_arg_locations = self._input_stat_locations[in_stat_name]
                for in_arg_loc in in_arg_locations:
                    func_args[in_arg_loc] = stat_value
            # if there is at least one non-None arg then convert the Nones to
            # zero and try to do the computation. If all are None then skip it.
            if self._null_to_zero(func_args) is False:
                # failed to get this stat, so return error for it
                derived_stat = \
                        self._create_derived_stat(None, node,
                                "Failed to get equation input for %s, " \
                                "input params: %s." % \
                                (self.out_stat_name, tuple(func_args)))
            else:
                try:
                    func_args_tuple = tuple(func_args)
                    LOG.debug("EQS [%s]=%s(%s)",
                            str(node), str(self._eq_func), str(func_args_tuple))
                    derived_stat_value = self._eq_func(*func_args_tuple)
                    derived_stat = \
                            self._create_derived_stat(derived_stat_value, node)
                except Exception as exception:
                    derived_stat = \
                            self._create_derived_stat(None, node,
                                error="Exception caught evaluating " \
                                        "expression for %s, input " \
                                        "params: %s, exception: %s" % \
                                        (self.out_stat_name,
                                            str(func_args_tuple),
                                            str(exception)))
            derived_stats.append(derived_stat)

        return derived_stats


    def _null_to_zero(self, func_args):
        null_args = []
        # since we don't know the type do some math to get zero in the correct
        # data type from one of the non-zero values
        zero = None
        for aindex in range(0, self._num_func_args):
            farg = func_args[aindex]
            if farg is None:
                null_args.append(aindex)
            else:
                zero = farg - farg

        if len(null_args) == self._num_func_args:
            # all the args are null so return False - we can't compute this
            # equation
            return False
        # go back through and set null args to zero
        for aindex in null_args:
            func_args[aindex] = zero

        return True


    def _get_stat_value(self, stat_name, node):
        try:
            return self._selected_stat_values[stat_name][node]
        except KeyError:
            return None


class PercentChangeStatComputer(DerivedStatComputer):
    def __init__(self, input_stat, out_stat_name):
        super(PercentChangeStatComputer, self).__init__(out_stat_name)
        self._input_stat = input_stat
        # per node/cluster value
        self._cur_values = {}
        self._prev_values = {}


    def begin_process(self, cluster_name):
        super(PercentChangeStatComputer, self).begin_process(cluster_name)
        self._cur_cluster_name = cluster_name
        self._cur_values = {}


    def end_process(self, cluster_name):
        super(PercentChangeStatComputer, self).end_process(cluster_name)
        self._prev_values[cluster_name] = self._cur_values


    def select_stat(self, stat):
        if stat.key == self._input_stat.name:
            self._cur_values[stat.devid] = \
                    self._input_stat.get_value(stat.value)
            self._choose_stat(stat)


    def compute_derived_stats(self):
        derived_stats = []
        for node in self._cur_values:
            try:
                cur_value = self._cur_values[node]
            except KeyError:
                cur_value = None
            if cur_value is None:
                derived_stat = \
                        self._create_derived_stat(None, node,
                                error="Unable to determine current value " \
                                        "of input stat: %s" \
                                        % self._input_stat.full_name)
            else:
                try:
                    prev_values = self._prev_values[self._cur_cluster_name]
                    # TREAT no previous value as zero?
                    prev_value = prev_values[node]
                    LOG.debug("PCS [%s]=(%s /  %s) - 1",
                            str(node), str(cur_value), str(prev_value))
                    try:
                        derived_stat_value = \
                                (float(cur_value) / float(prev_value)) - 1
                    except ZeroDivisionError:
                        if cur_value == 0 or cur_value == 0.0:
                            # prev_value and cur_value == 0
                            derived_stat_value = 0.0
                        else:
                            derived_stat_value = \
                                    (float(prev_value) / float(cur_value)) - 1
                            derived_stat_value *= -1.0
                    derived_stat_value *= 100.0
                except KeyError:
                    # no previous value will cause a KeyError
                    # so return 0% change
                    derived_stat_value = 0.0
                derived_stat = \
                        self._create_derived_stat(derived_stat_value, node)
            derived_stats.append(derived_stat)

        return derived_stats


class StatsConfig(object):
    def __init__(self, cluster_configs, stats, update_interval):
        self.cluster_configs = cluster_configs
        self.stats = stats
        self.update_interval = update_interval
        self.cluster_composite_stats = []
        self.equation_stats = []
        self.pct_change_stats = []
        self.final_equation_stats = []


class StatSet(object):
    def __init__(self):
        self.cluster_configs = []
        self.stats = set()
        self.cluster_composite_stats = []
        self.equation_stats = []
        self.pct_change_stats = []
        self.final_equation_stats = []


class UpdateInterval(object):
    def __init__(self, interval):
        self.interval = interval
        self.last_update = 0.0


class IsiDataInsightsDaemon(run.RunDaemon):
    """
    Periodically query a list of OneFS clusters for statistics and
    process them via a configurable stats processor module.
    """
    def __init__(self, pidfile):
        """
        Initialize.
        :param: pidfile is the path to the daemon's pidfile (required).
        """
        super(IsiDataInsightsDaemon, self).__init__(pidfile=pidfile)
        self._stat_sets = {}
        self._update_intervals = []
        self._stats_processor = None
        self._stats_processor_args = None
        self._process_stats_func = None
        self.async_worker_pool = gevent.pool.Pool(MAX_ASYNC_QUERIES)


    def set_stats_processor(self, stats_processor, processor_args):
        self._stats_processor = stats_processor
        self._stats_processor_args = processor_args
        if hasattr(stats_processor, 'process_stat') is True:
            self._process_stats_func = self._process_stats_with_derived_stats
            self._init_derived_stats_processor()
        elif hasattr(stats_processor, 'process') is True:
            self._process_stats_func = self._process_all_stats
        else:
            raise AttributeError(
                    "Results processor module has no process() or " \
                            "process_stat() function.")
        # start the stats processor module
        if hasattr(self._stats_processor, 'start') is True:
            # need to start the processor now before the process is daemonized
            # in case the plugin needs to prompt the user for input prior to
            # starting.
            LOG.info("Starting stats processor.")
            self._stats_processor.start(self._stats_processor_args)


    def _init_derived_stats_processor(self):
        # if the stats processor doesn't define begin_process or end_process,
        # then add a noop version so we don't have to check each time we
        # process stats
        def noop(cluster_name):
            pass
        if hasattr(self._stats_processor, 'begin_process') is False:
            self._stats_processor.begin_process = noop
        if hasattr(self._stats_processor, 'end_process') is False:
            self._stats_processor.end_process = noop


    def add_stats(self, stats_config):
        """
        Add set of stats to be queried.
        :param: stats_config is an instance of StatsConfig, which defines the
        list of stats, an update interval, and the list of clusters to query.
        """
        try:
            # organize the stat sets by update interval
            stat_set = self._stat_sets[stats_config.update_interval]
        except KeyError:
            self._stat_sets[stats_config.update_interval] = stat_set = \
                    StatSet()
            self._update_intervals.append(
                    UpdateInterval(stats_config.update_interval))

        # add the new clusters to the list of clusters associated with this
        # update interval's stat set.
        for cluster in stats_config.cluster_configs:
            if cluster not in stat_set.cluster_configs:
                # TODO this is a bug - this causes these stats to be queried on
                # all clusters in this update interval, not just the clusters
                # defined in this stats_config
                stat_set.cluster_configs.append(cluster)

        # add the new stats to the stat set
        for stat_name in stats_config.stats:
            stat_set.stats.add(stat_name)

        stat_set.cluster_composite_stats.extend(
                stats_config.cluster_composite_stats)

        stat_set.equation_stats.extend(stats_config.equation_stats)

        stat_set.pct_change_stats.extend(stats_config.pct_change_stats)

        stat_set.final_equation_stats.extend(stats_config.final_equation_stats)


    def get_stat_set_count(self):
        return len(self._stat_sets)


    def get_next_stat_set(self):
        for update_interval, stat_set in self._stat_sets.iteritems():
            yield update_interval, stat_set


    def run(self, debug=False):
        """
        Loop through stat sets, query for their values, and process them with
        the stats processor.
        """
        LOG.info("Starting.")

        sleep_secs = 0
        start_time = time.time()
        # setup the last update time of each update interval so that they all
        # get updated on the first pass.
        for update_interval in self._update_intervals:
            update_interval.last_update = start_time - update_interval.interval

        while True:
            LOG.debug("Sleeping for %f seconds.", sleep_secs)
            time.sleep(sleep_secs)

            # query and process the stat sets whose update interval has been
            # hit or surpassed.
            self._query_and_process_stats(time.time(), debug)

            cur_time = time.time()
            # figure out the shortest amount of time until the next update is
            # needed and sleep for that amount of time.
            min_next_update = sys.float_info.max
            for update_interval in self._update_intervals:
                next_update_time = \
                        update_interval.last_update + update_interval.interval

                time_to_next_update = next_update_time - cur_time
                min_next_update = min(time_to_next_update, min_next_update)
            sleep_secs = max(0.0, min_next_update)


    def shutdown(self, signum):
        """
        Stops the stats processor prior to stopping the daemon.
        """
        LOG.info("Stopping.")
        if self._stats_processor is not None \
                and hasattr(self._stats_processor, 'stop') is True:
            LOG.info("Stopping stats processor.")
            self._stats_processor.stop()
        super(IsiDataInsightsDaemon, self).shutdown(signum)


    def _query_and_process_stats(self, cur_time, debug):
        """
        Build a unique set of stats to update per cluster from each set of
        stats that are in need of updating based on the amount of time elapsed
        since their last update.
        """
        # there might be more than one stat set that needs updating and thus
        # there might be common clusters between those stat sets, so this loop
        # makes sure that we only send one query to each unique cluster.
        cluster_stats = {}
        for update_interval in self._update_intervals:
            # if the update_interval is less than or equal to the elapsed_time
            # then we need to query the stats associated with this update
            # interval.
            time_since_last_update = cur_time - update_interval.last_update
            if time_since_last_update >= update_interval.interval:
                LOG.debug("updating interval:%d time_since_last_update: %f",
                        update_interval.interval, time_since_last_update)
                # update the last_update time
                update_interval.last_update = cur_time
                # add the stats from stat set to their respective cluster_stats
                cur_stat_set = self._stat_sets[update_interval.interval]
                for cluster in cur_stat_set.cluster_configs:
                    try:
                        (cluster_stat_set,
                                cluster_composite_stats,
                                equation_stats,
                                pct_change_stats,
                                final_equation_stats) = \
                                        cluster_stats[cluster]
                        cluster_composite_stats.extend(
                                cur_stat_set.cluster_composite_stats)
                        equation_stats.extend(
                                cur_stat_set.equation_stats)
                        pct_change_stats.extend(
                                cur_stat_set.pct_change_stats)
                        final_equation_stats.extend(
                                cur_stat_set.final_equation_stats)
                    except KeyError:
                        cluster_stat_set = set()
                        cluster_stats[cluster] = (
                                cluster_stat_set,
                                cur_stat_set.cluster_composite_stats,
                                cur_stat_set.equation_stats,
                                cur_stat_set.pct_change_stats,
                                cur_stat_set.final_equation_stats)

                    for stat_name in cur_stat_set.stats:
                        cluster_stat_set.add(stat_name)

        # now we have a unique list of clusters to query, so query them
        for cluster, (stats,
                composite_stats, eq_stats,
                pct_change_stats, final_eq_stats) in cluster_stats.iteritems():
            self.async_worker_pool.spawn(
                    self._query_and_process_stats1, cluster, stats,
                    composite_stats, eq_stats,
                    pct_change_stats, final_eq_stats)
        self.async_worker_pool.join()

    def _query_and_process_stats1(self, cluster, stats, composite_stats,
            eq_stats, pct_change_stats, final_eq_stats):
        LOG.debug("Querying cluster %s %f", cluster.name, cluster.version)
        LOG.debug("Querying stats %d.", len(stats))
        stats_client = \
                IsiStatsClient(
                        cluster.isi_sdk.StatisticsApi(cluster.api_client))
        # query the current cluster with the current set of stats
        try:
            if cluster.version >= 8.0:
                results = stats_client.query_stats(stats)
            else:
                results = \
                        self._v7_2_multistat_query(
                                stats, stats_client)
        except (urllib3.exceptions.HTTPError,
                cluster.isi_sdk.rest.ApiException) as http_exc:
            LOG.error("Failed to query stats from cluster %s, exception "\
                      "raised: %s", cluster.name, str(http_exc))
            return
        except Exception as gen_exc:
            # if in debug mode then re-raise general Exceptions because
            # they are most likely bugs in the code, but in non-debug mode
            # just continue
            if debug is False:
                LOG.error("Failed to query stats from cluster %s, exception "\
                          "raised: %s", cluster.name, str(gen_exc))
                return
            else:
                raise gen_exc

        composite_stats_processor = \
                DerivedStatsProcessor(composite_stats)
        equation_stats_processor = \
                DerivedStatsProcessor(eq_stats)
        pct_change_stats_processor = \
                DerivedStatsProcessor(pct_change_stats)
        final_equation_stats_processor = \
                DerivedStatsProcessor(final_eq_stats)
        derived_stats_processors = \
                (composite_stats_processor,
                        equation_stats_processor,
                        pct_change_stats_processor,
                        final_equation_stats_processor)
        # calls either _process_all_stats or
        # _process_stats_with_derived_stats depending on whether or not the
        # _stats_processor has a process_stat function or just a process
        # function. The latter requires the process_stat function.
        self._process_stats_func(
                cluster.name, results, derived_stats_processors)


    def _v7_2_multistat_query(self, stats, stats_client):
        result = []
        for stat in stats:
            result.extend(stats_client.query_stat(stat))
        return result


    def _process_all_stats(self, *args):
        cluster_name = args[0]
        results = args[1]
        # the initial version of the stats processor plugin processed all stats
        # at once, this function allows backwards compatibility, but derived
        # stats are not supported
        self._stats_processor.process(cluster_name, results)


    def _process_stats_with_derived_stats(
            self, cluster_name, stats_query_results, derived_stats):
        LOG.debug("Processing stat results on %s", cluster_name)
        self._stats_processor.begin_process(cluster_name)
        (cluster_composite_stats,
                equation_stats,
                pct_change_stats,
                final_equation_stats) = derived_stats
        cluster_composite_stats.begin_process(cluster_name)
        equation_stats.begin_process(cluster_name)
        pct_change_stats.begin_process(cluster_name)
        final_equation_stats.begin_process(cluster_name)
        # process the results
        for stat in stats_query_results:
            # check if the stat query returned an error
            if stat.error is not None:
                LOG.warning("Query for stat: '%s' on '%s', returned error: '%s'.",
                        str(stat.key), cluster_name, str(stat.error))
                continue
            self._prep_stat(stat)
            # let stats processor process it
            self._stats_processor.process_stat(cluster_name, stat)
            # allow derived stats to select/use this stat
            cluster_composite_stats.select_stat(stat)
            equation_stats.select_stat(stat)
            pct_change_stats.select_stat(stat)
            final_equation_stats.select_stat(stat)

        LOG.debug("Processing composite stats on %s", cluster_name)
        for composite_stat in cluster_composite_stats.stats():
            # composite stats always return only one derived stat
            derived_stat = composite_stat.compute_derived_stat()
            if derived_stat.error is not None:
                LOG.warning("Cluster node composite stat: " \
                        "'%s' on '%s', returned error: '%s'.",
                        str(derived_stat.key),
                        cluster_name,
                        str(derived_stat.error))
                continue
            LOG.debug("ClusterCompositeStat[%s]=%s",
                    derived_stat.key, str(derived_stat.value))
            # let stats processor process it
            self._stats_processor.process_stat(cluster_name, derived_stat)
            # allow derived stats to select/use this stat
            equation_stats.select_stat(derived_stat)
            pct_change_stats.select_stat(derived_stat)
            final_equation_stats.select_stat(derived_stat)

        LOG.debug("Processing equation stats on %s", cluster_name)
        for eq_stat in equation_stats.stats():
            # equation stats might produce more than one derived stat,
            # potentially one stat per node
            derived_stats = eq_stat.compute_derived_stats()
            for derived_stat in derived_stats:
                if derived_stat.error is not None:
                    LOG.warning("Equation computed stat: " \
                            "'%s' on '%s', returned error: '%s'.",
                        str(derived_stat.key),
                        cluster_name,
                        str(derived_stat.error))
                    continue
                LOG.debug("EquationStat[%s]=%s",
                        derived_stat.key, str(derived_stat.value))
                # let stats processor process them
                self._stats_processor.process_stat(cluster_name, derived_stat)
                # allow derived stats to select/use this stat
                pct_change_stats.select_stat(derived_stat)
                final_equation_stats.select_stat(derived_stat)

        LOG.debug("Processing percent change stats on %s", cluster_name)
        for pct_change_stat in pct_change_stats.stats():
            # percent change stats might produce more than one derived stat,
            # potentially one stat per node
            derived_stats = pct_change_stat.compute_derived_stats()
            for derived_stat in derived_stats:
                if derived_stat.error is not None:
                    LOG.warning("Percent change stat: " \
                            "'%s' on '%s', returned error: '%s'.",
                        str(derived_stat.key),
                        cluster_name,
                        str(derived_stat.error))
                    continue
                LOG.debug("PercentChangeStat[%s]=%s",
                        derived_stat.key, str(derived_stat.value))
                # let stats processor process it
                self._stats_processor.process_stat(cluster_name, derived_stat)
                # allow derived stats to select/use this stat
                final_equation_stats.select_stat(derived_stat)

        LOG.debug("Processing final equation stats on %s", cluster_name)
        for eq_stat in final_equation_stats.stats():
            # equation stats might produce more than one derived stat,
            # potentially one stat per node
            derived_stats = eq_stat.compute_derived_stats()
            for derived_stat in derived_stats:
                if derived_stat.error is not None:
                    LOG.warning("Final equation computed stat: " \
                            "'%s' on '%s', returned error: '%s'.",
                        str(derived_stat.key),
                        cluster_name,
                        str(derived_stat.error))
                    continue
                LOG.debug("FinalEquationStat[%s]=%s",
                        derived_stat.key, str(derived_stat.value))
                # let stats processor process them
                self._stats_processor.process_stat(cluster_name, derived_stat)

        self._stats_processor.end_process(cluster_name)
        cluster_composite_stats.end_process(cluster_name)
        equation_stats.end_process(cluster_name)
        pct_change_stats.end_process(cluster_name)
        final_equation_stats.end_process(cluster_name)


    def _prep_stat(self, stat):
        try:
            # the stat value's data type is variable depending on the key so
            # use literal_eval() to convert it to the correct type
            eval_value = literal_eval(stat.value)
            # convert tuples to a list for simplicity
            if type(eval_value) == tuple:
                stat.value = list(eval_value)
            else:
                stat.value = eval_value
        except Exception: # if literal_eval throws an exception
            # then just leave it as string value
            pass
