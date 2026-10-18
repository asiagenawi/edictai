# Read automatically when gunicorn runs with --chdir api.
#
# Access lines record the method, path (without query string), status, size,
# and duration only: no client address, no query string, no request body.
accesslog = '-'
access_log_format = '"%(m)s %(U)s" %(s)s %(B)s %(L)ss'
