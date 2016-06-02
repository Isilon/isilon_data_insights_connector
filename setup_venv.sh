#!/bin/bash

set -e

version=$1

venv_path=".venv"$version

pip install virtualenv

if [ -f $venv_path ]
then
    echo "venv found, no need to create it"
else
    virtualenv $venv_path
fi

. $venv_path/bin/activate
pip install -r requirements.txt
if [ "$version" -eq '7' ]
then
    pip install git+https://github.com/Isilon/isilon_sdk_7_2_python.git
else
    pip install git+https://github.com/Isilon/isilon_sdk_8_0_python.git
fi

echo
echo "Isilon Data Insights Connector virtual environment setup at $venv_path."
echo "To activate the virtual environment run: . $venv_path/bin/activate"
