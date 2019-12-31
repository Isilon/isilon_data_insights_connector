from builtins import range
from builtins import object
import logging


LOG = logging.getLogger(__name__)
# Apache/PAPI has a request URI limit of 8096, MAX_KEYS_LEN is the max
# length of a set of keys that the client will attempt to send.
MAX_KEYS_LEN = 7000
# When getting metadata for multiple stats, if there are less than
# MAX_DIRECT_METADATA_STATS then do the query as multiple direct key queries,
# otherwise do it as a single batch query and filter the results on the client
# side. Testing revealed that 200 is the optimal cutoff point for a virtual
# cluster.
MAX_DIRECT_METADATA_STATS = 200


class IsiStatsClient(object):
    """
    Handles the details of querying for Isilon cluster statistics values and
    metadata using the Isilon SDK.
    """

    def __init__(self, stats_api):
        """
        Setup the Isilon SDK to query the specified cluster's statistics.
        :param StatisticsApi stats_api: instance of StatisticsApi from the
        isi_sdk_8_0 or isi_sdk_7_2 package.
        """
        # get the Statistics API
        self._stats_api = stats_api

    def query_stats(
        self,
        stats,
        devid="all",
        substr=False,
        timeout=60,
        degraded=True,
        expand_clientid=False,
    ):
        """
        Queries the cluster for a list of stat values. Note: this function only
        works on OneFS 8.0 or newer.
        :param list stats: a list of stat names to query
        :param string devid: The node number or "all" to query all nodes.
        :param bool substr: If True, makes the 'keys' arg perform a partial
        match.
        :param int timeout: Time in seconds to wait for results from remote
        nodes.
        :param bool degraded: If true, try to continue even if some stats are
        unavailable.
        :param bool expand_clientid: If true, use name resolution to expand
        client addresses and other IDs.
        :returns: a list of isi_sdk.models.StatisticsCurrentStat
        instances corresponding to the list of stat names provided in the stats
        input list.
        """
        # setup the stat keys for querying as set of comma delimitted values
        combined_query_results = None
        stat_keys = ",".join(stats)
        stat_index = 0
        stat_keys_len = len(stat_keys)
        while stat_index < stat_keys_len:
            if stat_keys_len - stat_index > MAX_KEYS_LEN:
                # find the last comma between stat_index and
                # stat_index + MAX_KEYS_LEN
                next_stat_index = stat_keys.rfind(
                    ",", stat_index, stat_index + MAX_KEYS_LEN
                )
                # unless there's a key that is longer than MAX_KEYS_LEN
                # then the rfind should never return -1 because there should
                # definitely be at least one comma.
                query_keys = stat_keys[stat_index:next_stat_index]
                stat_index = next_stat_index + 1
            else:
                query_keys = stat_keys[stat_index:]
                stat_index = stat_keys_len

            query_result = self._stats_api.get_statistics_current(
                keys=query_keys,
                devid=devid,
                substr=substr,
                degraded=degraded,
                expand_clientid=expand_clientid,
                timeout=timeout,
            )

            if combined_query_results is None:
                combined_query_results = query_result
            else:
                combined_query_results.stats.extend(query_result.stats)

        # return the list of stats only (at this point there are no other
        # fields on the query_results data model).
        return combined_query_results.stats

    def query_stat(
        self, stat, devid="all", timeout=60, degraded=True, expand_clientid=False
    ):
        """
        Queries the cluster for a single stat's value. Note: this function
        works on OneFS 7.2 or newer clusters.
        :param string stats: the name of the stat to query
        :param string devid: The node number or "all" to query all nodes.
        :param int timeout: Time in seconds to wait for results from remote
        nodes.
        :param bool degraded: If true, try to continue even if some stats are
        unavailable.
        :param bool expand_clientid: If true, use name resolution to expand
        client addresses and other IDs.
        :returns: an instance of isi_sdk.models.StatisticsCurrentStat
        """
        query_result = self._stats_api.get_statistics_current(
            key=stat,
            devid=devid,
            degraded=degraded,
            expand_clientid=expand_clientid,
            timeout=timeout,
        )

        return query_result.stats

    def get_stats_metadata(self, stats=None):
        """
        Query the cluster for the metadata associated with each key specified
        in the stats list or all stats if stats is None.
        :param list stats: list of statistic keys to query.
        :returns: a list of isi_sdk.models.StatisticsKey instances (in
        the same order as the stats input param list).
        """
        if stats is not None and len(stats) < MAX_DIRECT_METADATA_STATS:
            return self._get_metadata_direct(stats)
        return self._get_metadata_indirect(stats)

    def get_stat_metadata(self, stat):
        """
        Query the cluster for the metadata of a specific stat.
        :param string stat: the name of the stat to query
        :returns: a single isi_sdk.models.StatisticsKey.
        """
        result = self._stats_api.get_statistics_key(statistics_key_id=stat)
        return result.keys[0]

    def _get_metadata_indirect(self, stats):
        """
        Get the metadata for every single stat and then filter it down to the
        list of stats specified in the stats param.
        :param list stats: the list of stats to return metadata for, or if it
        is None then return all metadata.
        :returns: a list of isi_sdk.models.StatisticsKey instances.
        """
        stat_map = {}
        if stats is not None:
            num_stats = len(stats)
            for stat_index in range(0, num_stats):
                stat_map[stats[stat_index]] = stat_index
            result_list = [None] * num_stats
        else:
            result_list = []
        query_args = dict()
        while True:
            results = self._stats_api.get_statistics_keys(**query_args)
            if stats is None:
                if result_list is None:
                    result_list = results.keys
                else:
                    result_list.extend(results.keys)
            else:
                for key in results.keys:
                    try:
                        stat_index = stat_map[key.key]
                        result_list[stat_index] = key
                        num_stats -= 1
                        if num_stats == 0:
                            break
                    except KeyError:
                        pass

            resume = results.resume
            if resume is None:
                break
            query_args["resume"] = resume

        return result_list

    def _get_metadata_direct(self, stats):
        """
        Get the metadata for the list of stats provided in the stats list input
        parameter by sending an individual request for each stat. When the list
        of stats is small(er) then this method is faster than querying for all
        the stats metadata and filtering it (see _get_metadata_indirect).
        :param list stats: the list of stat names to query for metadata.
        :returns: a list of isi_sdk.models.StatisticsKey instances.
        """
        metadata_list = []
        for stat in stats:
            metadata = self.get_stat_metadata(stat)
            metadata_list.append(metadata)
        return metadata_list
