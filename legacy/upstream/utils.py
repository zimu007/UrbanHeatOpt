#!interpreter [optional-arg]
# -*- coding: utf-8 -*-

from datetime import datetime
import logging
import os
import pathlib
import shutil
import sys
import time
import yaml


def clear_logs(config: dict):
    """Clears the logs directory specified in the configuration dictionary.

    :param config: location of the logs directory
    :type config: dict
    """

    path_logs = config['log_dir']
    if os.path.exists(path_logs):
        shutil.rmtree(path_logs)
        os.makedirs(path_logs)
    else:
        logging.warning("No logs directory found!")



def create_logger(name:str, logs_folder: str) -> logging.Logger:
    """Creates a logger that writes to a file in the specified logs folder. Uses the name parameter and a timestamp
     to create the current the logger and returns it.

    :param name: name of the logger object.
    :type name: str
    :param logs_folder: location of the logs folder where the log file will be created.
    :type logs_folder: str
    :return: logger object that writes to a file in the specified logs folder.
    :rtype: logging.Logger
    """

    if not os.path.exists(logs_folder):
        os.makedirs(logs_folder)

    timestamp = datetime.today().strftime("%Y-%M-%d %HH.%MM.%SS")
    log_file = f"{name}_{timestamp}.log"
    log_path = os.path.join(logs_folder, log_file)

    # create logger
    logging.basicConfig(filename=log_path, format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO,
                        datefmt='%Y-%m-%d %H:%M:%S')
    logger = logging.getLogger(name)

    return logger






