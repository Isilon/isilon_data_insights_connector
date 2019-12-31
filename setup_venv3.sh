#!/bin/bash

set -e

venv_path=".venv3"

python3 -m venv $venv_path

. $venv_path/bin/activate
pip install -r requirements.txt

echo
echo "Isilon Data Insights Connector virtual environment setup at $venv_path."
echo "To activate the virtual environment run: . $venv_path/bin/activate"
