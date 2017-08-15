#!/usr/bin/env python

# Have to do this before importing the other libs
# The noqa comment prevents spurious E402 flake8 errors
# The documentation for monkey explicitly requires patching to be
# performed as early as possible BEFORE other imports
from gevent import monkey
monkey.patch_all()  # noqa

import sys

from isi_data_insights_config import parse_cli, \
        configure_args_via_file, process_pid_file_arg, \
        configure_logging_via_cli, configure_via_cli, \
        configure_via_file
from isi_data_insights_daemon import IsiDataInsightsDaemon


def main():
    args = parse_cli()

    # load the config file if one is provided, then set the "required"
    # parameters of the CLI args with config file parameters (if possible)
    config_file = configure_args_via_file(args)

    # validate the pid_file arg and get the full path to it.
    pid_file_path = process_pid_file_arg(args.pid_file, args.action)

    daemon = IsiDataInsightsDaemon(pidfile=pid_file_path)

    # before we do the long process of configuring, lets make sure we have
    # a valid pid to do a stop or restart with
    if (args.action == "restart" or args.action == "stop") \
            and daemon.pid is None:
        print >> sys.stderr, "Cannot " + args.action + " daemon, " \
                "invalid pid in file: " + str(pid_file_path)
        sys.exit(1)

    if args.action == "start" \
            or args.action == "debug" \
            or args.action == "restart":
        configure_logging_via_cli(args)

        if config_file is not None:
            configure_via_file(daemon, args, config_file)
        else:
            configure_via_cli(daemon, args)

        if args.action == "start":
            daemon.start()
        elif args.action == "restart":
            print "Restarting daemon with pid " + str(daemon.pid)
            daemon.restart()
        else:
            daemon.run(debug=True)
    elif args.action == "stop":
        print "Stopping daemon with pid " + str(daemon.pid)
        daemon.stop()
    else:
        print >> sys.stderr, "Invalid action arg: '%s', must be one of "\
                "'start', 'stop', or 'restart'." % args.action


if __name__ == "__main__":
    main()
