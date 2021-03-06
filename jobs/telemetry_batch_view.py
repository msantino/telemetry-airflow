#!/usr/bin/env python

import requests
from os import chdir
from os import environ
from subprocess import call, PIPE, Popen
from urlparse import urlparse
import zipfile
import boto3

artifact_file = "artifact.jar"


def call_exit_errors(command):
    print("+ {}".format(" ".join(command)))
    rc = call(command, env=environ.copy())
    if rc > 0:
       exit(rc)


def retrieve_jar():
    jar_url = environ.get("ARTIFACT_URL")

    if jar_url is None:
        exit(1)


    print("Retrieving JAR: {}".format(jar_url))

    # Check to see if this is an alias for a full jar path
    # If it's an alias, it should be accompanied by a .txt
    # file whose contents point to the aliased location.
    #
    # The associated .txt files have two lines [0]:
    # 1. The query string to get to the aliased jar
    # 2. The associated build URL for that jar
    #
    # Historical version only had the query string [1],
    # so we need to handle that case separately.
    #
    # [0] https://github.com/mozilla/telemetry-batch-view/blob/master/.circleci/deploy.sh#L37
    # [1] https://github.com/mozilla/telemetry-batch-view/blob/14741db20dd3873b94944b8238dfc48a003c744d/deploy.sh#L50

    txt_url = jar_url.replace(".jar", ".txt")
    response = requests.get(txt_url)

    if response.status_code != 404:
        uri_query, _, build_url = response.content.partition("\n")
        if not build_url:
            # Handle historical version
            build_url = "Build URL not available"

        parsed_uri = urlparse(jar_url)
        bucket, _, _ = parsed_uri.path.lstrip("/").partition("/")
        full_url = "{uri.scheme}://{uri.netloc}/{bucket}/{uri_query}".format(uri=parsed_uri, bucket=bucket, uri_query=uri_query)

        print("  Alias: {}".format(full_url))
        print("  Build URL: {}".format(build_url.strip()))

    response = requests.get(jar_url)
    with open(artifact_file, 'wb') as f:
        f.write(response.content)


def events_to_amplitude_setup():
    config_filename = environ.get("TBV_config_file_path")
    api_key_bucket = environ.get("KEY_BUCKET")
    api_key_path = environ.get("KEY_PATH")

    print("Extracting config file {}".format(config_filename))

    zfile = zipfile.ZipFile(artifact_file)
    data = zfile.read(config_filename)
    with open(config_filename, "w") as outfile:
        outfile.write(data)

    print("Setting amplitude key to contents of {}/{}".format(api_key_bucket,
                                                              api_key_path))
    s3 = boto3.resource('s3')
    obj = s3.Object(api_key_bucket, api_key_path)
    key = obj.get()['Body'].read().decode('utf-8').strip()
    environ["AMPLITUDE_API_KEY"] = key


def submit_job():
    opts = [
        ["--{}".format(key[4:].replace("_", "-")), value]
        for key, value in environ.items()
        if key.startswith("TBV_") and key != "TBV_CLASS"
    ]

    command = [
        "spark-submit",
        "--master", "yarn",
        "--deploy-mode", "client",
        "--class", environ["TBV_CLASS"],
        artifact_file,
    ] + [v for opt in opts for v in opt if v]

    call_exit_errors(command)


def update_metastore(location, hive_server):
    p2h_cmd = ("parquet2hive", "-ulv=1", "--sql", location)
    beeline_cmd = ("beeline", "-u", "jdbc:hive2://{}:10000".format(hive_server))
    print("+ {}".format(" ".join(p2h_cmd)))
    p2h = Popen(p2h_cmd, stdout=PIPE)
    print("+ {}".format(" ".join(beeline_cmd)))
    rc = call(beeline_cmd, stdin=p2h.stdout)
    if rc > 0:
        exit(rc)


if environ.get("DO_RETRIEVE", "True") == "True":
    retrieve_jar()

if environ.get("DO_EVENTS_TO_AMPLITUDE_SETUP") == "True":
    events_to_amplitude_setup()

if environ.get("DO_SUBMIT", "True") == "True":
    submit_job()

if environ.get("METASTORE_LOCATION") != None:
    update_metastore(environ.get("METASTORE_LOCATION"), environ.get("HIVE_SERVER"))
