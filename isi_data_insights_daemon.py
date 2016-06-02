from daemons.prefab import run
import logging
import sys
import time

from isi_stats_client import IsiStatsClient


LOG = logging.getLogger(__name__)


class ClusterConfig(object):
    def __init__(self,
            address, username, password, version, name=None, verify_ssl=False):
        self.address = address
        self.username = username
        self.password = password
        self.version = version
        if name is None:
            self.name = address
        else:
            self.name = name
        self.verify_ssl = verify_ssl


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


class StatsConfig(object):
    def __init__(self, cluster_configs, stats, update_interval):
        self.cluster_configs = cluster_configs
        self.stats = stats
        self.update_interval = update_interval


class StatSet(object):
    def __init__(self):
        self.cluster_configs = []
        self.stats = set()


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


    def set_stats_processor(self, stats_processor, processor_args):
        if hasattr(stats_processor, 'process') is False:
            raise AttributeError(
                    "Results processor module has no process() function.")
        self._stats_processor = stats_processor
        self._stats_processor_args = processor_args
        # start the stats processor module
        if hasattr(self._stats_processor, 'start') is True:
            # need to start the processor now before the process is daemonized
            # in case the plugin needs to prompt the user for input prior to
            # starting.
            LOG.info("Starting stats processor.")
            self._stats_processor.start(self._stats_processor_args)


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
                stat_set.cluster_configs.append(cluster)

        # add the new stats to the stat set
        for stat_name in stats_config.stats:
            stat_set.stats.add(stat_name)


    def get_stat_set_count(self):
        return len(self._stat_sets)


    def get_next_stat_set(self):
        for update_interval, stat_set in self._stat_sets.iteritems():
            yield update_interval, stat_set


    def run(self):
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
            self._query_and_process_stats(time.time())

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
        LOG.info("Stoppping.")
        if self._stats_processor is not None \
                and hasattr(self._stats_processor, 'stop') is True:
            LOG.info("Stopping stats processor.")
            self._stats_processor.stop()
        super(IsiDataInsightsDaemon, self).shutdown(signum)


    def _query_and_process_stats(self, cur_time):
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
                for stat_name in cur_stat_set.stats:
                    for cluster in cur_stat_set.cluster_configs:
                        try:
                            cluster_stat_set = cluster_stats[cluster]
                        except KeyError:
                            cluster_stats[cluster] = cluster_stat_set = set()
                        cluster_stat_set.add(stat_name)

        # now we have a unique list of clusters to query, so query them
        for cluster, stats in cluster_stats.iteritems():
            LOG.debug("Querying cluster %s.", cluster.name)
            LOG.debug("Querying stats %d.", len(stats))
            stats_client = \
                    IsiStatsClient(
                            cluster.address,
                            cluster.username,
                            cluster.password,
                            cluster.verify_ssl)
            # query the current cluster with the current set of stats
            try:
                if cluster.version >= 8.0:
                    results = stats_client.query_stats(stats)
                else:
                    results = []
                    for stat in stats:
                        result = stats_client.query_stat(stat)
                        results.append(result)

            except Exception as exc:
                LOG.error("Failed to query stats from cluster %s, exception "\
                          "raised: %s", cluster.name, str(exc))
                continue
            # process the results
            self._stats_processor.process(cluster.name, results)
