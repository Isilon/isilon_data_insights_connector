#!/bin/bash

set -e

venv_path=".venv3"

pip install --user virtualenv

virtualenv -p python3 $venv_path

. $venv_path/bin/activate
pip3 install -r requirements.txt

echo
echo "Isilon Data Insights Connector virtual environment setup at $venv_path."
echo "To activate the virtual environment run: . $venv_path/bin/activate"
