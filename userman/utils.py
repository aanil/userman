" Userman: Various utility functions. "

import os
import socket
import logging
import urllib.parse
import uuid
import hashlib
import datetime
import unicodedata

from ibmcloudant import CouchDbSessionAuthenticator, cloudant_v1
import ibm_cloud_sdk_core
import yaml

from userman import constants
from userman import settings

class CloudantDatabaseWrapper:
    # Wrap a CloudantV1 client to provide a minimal CouchDB-like database interface.
    def __init__(self, client, db_name):
        self.client = client
        self.db_name = db_name
    
    def __getitem__(self, doc_id):
        """Mimics db['doc_id']"""
        response = self.client.get_document(
            db=self.db_name,
            doc_id=doc_id
        ).get_result()
        return response
    
    def __iter__(self):
        """Make the database wrapper iterable by document IDs"""
        try:
            # Get all document IDs using _all_docs view
            response = self.client.post_all_docs(
                db=self.db_name,
                include_docs=False  # Only get IDs, not full documents
            ).get_result()
            
            for row in response.get('rows', []):
                yield row['id']
        except Exception as e:
            logging.error(f"Error iterating database: {e}")
            return
    
    def view(self, viewname, **options):
        """Mimics db.view(...)"""
        ddoc, view = viewname.split('/')
        response = self.client.post_view(
            db=self.db_name,
            ddoc=ddoc,
            view=view,
            **options
        ).get_result()
        return response.get('rows', [])
    
    def save(self, document):
        """Mimics db.save(doc)"""
        if '_id' in document:
            doc_id = document['_id']
        else:
            doc_id = uuid.uuid4().hex
            document['_id'] = doc_id
        response = self.client.put_document(
            db=self.db_name,
            doc_id=doc_id,
            document=document
        ).get_result()
        return response
    
    def delete(self, document):
        """Mimics db.delete(doc)"""
        if '_id' not in document or '_rev' not in document:
            raise ValueError("Document must have '_id' and '_rev' to be deleted")
        response = self.client.delete_document(
            db=self.db_name,
            doc_id=document['_id'],
            rev=document['_rev']
        ).get_result()
        return response

    def get_attachemnt(self, doc_id, attachment_name):
        """Get an attachment from a document."""
        response = self.client.get_attachment(
            db=self.db_name,
            doc_id=doc_id,
            attachment_name=attachment_name
        ).get_result()
        return response
    
    def put_attachment(self, doc_id, data, attachment_name, content_type, rev):
        """Put an attachment to a document."""
        response = self.client.put_attachment(
            db=self.db_name,
            doc_id=doc_id,
            attachment=data,
            attachment_name=attachment_name,
            content_type=content_type,
            rev=rev
        ).get_result()
        return response


def load_settings(filepath=None):
    """Load the settings from the given settings file, or from the first
    existing file in a predefined list of filepaths.
    Raise IOError if no readable settings file was found.
    Raise KeyError if a settings variable is missing.
    Raise ValueError if the settings variable value is invalid."""
    homedir = os.path.expandvars('$HOME')
    basedir = os.path.dirname(__file__)
    localdir = '/var/local/userman'
    if not filepath:
        hostname = socket.gethostname().split('.')[0]
        for filepath in [os.path.join(homedir, "{0}.yaml".format(hostname)),
                         os.path.join(homedir, 'default.yaml'),
                         os.path.join(basedir, "{0}.yaml".format(hostname)),
                         os.path.join(basedir, 'default.yaml'),
                         os.path.join(localdir, "{0}.yaml".format(hostname)),
                         os.path.join(localdir, 'default.yaml')]:
            if os.path.exists(filepath) and \
               os.path.isfile(filepath) and \
               os.access(filepath, os.R_OK):
                break
        else:
            raise IOError('no readable settings file found')
    with open(filepath) as infile:
        settings.update(yaml.safe_load(infile))
    # Set logging state
    if settings.get('LOGGING_DEBUG'):
        kwargs = dict(level=logging.DEBUG)
    else:
        kwargs = dict(level=logging.INFO)
    try:
        kwargs['format'] = settings['LOGGING_FORMAT']
    except KeyError:
        pass
    try:
        kwargs['filename'] = settings['LOGGING_FILENAME']
    except KeyError:
        pass
    try:
        kwargs['filemode'] = settings['LOGGING_FILEMODE']
    except KeyError:
        pass
    logging.basicConfig(**kwargs)
    logging.info("settings from file %s", filepath)
    # Check settings
    for key in ['BASE_URL', 'DB_SERVER', 'DB_DATABASE',
                'COOKIE_SECRET', 'HASH_SALT',
                'ACTIVATION_EMAIL', 'RESET_EMAIL']:
        if key not in settings:
            raise KeyError("no '{0}' key in settings".format(key))
        if not settings[key]:
            raise ValueError("setting '{0}' has invalid value".format(key))
    if len(settings['COOKIE_SECRET']) < 10:
        raise ValueError('setting COOKIE_SECRET too short')
    # Prepend source code base dir to relative filepaths
    for key in ['ACTIVATION_EMAIL', 'RESET_EMAIL']:
        if not os.path.isabs(settings[key]):
            settings[key] = os.path.join(basedir, settings[key])
    # Settings computable from others
    settings['DB_SERVER_VERSION'] = get_couchdb_client().get_server_information().get_result().get("version")
    if 'PORT' not in settings:
        parts = urllib.parse.urlparse(settings['BASE_URL'])
        items = parts.netloc.split(':')
        if len(items) == 2:
            settings['PORT'] = int(items[1])
        elif parts.scheme == 'http':
            settings['PORT'] =  80
        elif parts.scheme == 'https':
            settings['PORT'] =  443
        else:
            raise ValueError('could not determine port from BASE_URL')

def get_couchdb_client():
    "Return the handle for the CouchDB server."
    try:
        cloudant = cloudant_v1.CloudantV1(
            authenticator=CouchDbSessionAuthenticator(
                settings.get("DB_USERNAME"), settings.get("DB_PASSWORD")
            )
        )
        cloudant.set_service_url(settings.get("DB_SERVER"))
        return cloudant
    except Exception as e:
        raise KeyError("Could not connect to CouchDB server: %s" % str(e))

def get_db():
    "Return the handle for the CouchDB database."
    try:
        return CloudantDatabaseWrapper(get_couchdb_client(), settings['DB_DATABASE'])
    except ibm_cloud_sdk_core.api_exception.ApiException:
        raise KeyError("CouchDB database '%s' does not exist" %
                       settings['DB_DATABASE'])

def get_user_doc(db, name):
    """Get the document for the account given by name (email or username).
    Raise ValueError if no such account."""
    # 'name' is the email address for the account
    if '@' in name:
        viewname = 'user/email'
    # else 'name' is the username for the account
    else:
        viewname = 'user/username'
    result = list(db.view(viewname, include_docs=True, key=name))
    if len(result) != 1:
        raise ValueError("no such user account '{0}'".format(name))
    return result[0]['doc']

def get_iuid():
    "Return a unique instance identifier."
    return uuid.uuid4().hex

def timestamp(days=None):
    """Current date and time (UTC) in ISO format, with millisecond precision.
    Add the specified offset in days, if given."""
    instant = datetime.datetime.utcnow()
    if days:
        instant += datetime.timedelta(days=days)
    instant = instant.isoformat()
    return instant[:-9] + "%06.3f" % float(instant[-9:]) + "Z"

def to_ascii(value):
    "Convert any non-ASCII character to its closest equivalent."
    if not isinstance(value, str):
        value = str(value, 'utf-8')
    return unicodedata.normalize('NFKD', value).encode('ascii', 'ignore')

def to_bool(value):
    " Convert the value into a boolean, interpreting various string values."
    if not value: return False
    value = value.lower()
    return value in ['true', 'yes'] or value[0] in ['t', 'y']

def check_password_quality(password):
    "Check password quality. Raise ValueError if insufficient."
    if len(password) < 6:
        raise ValueError('password shorter than 6 characters')

def hashed_password(password):
    "Hash the password."
    m = hashlib.sha384(settings['HASH_SALT'].encode('utf-8'))
    m.update(password.encode('utf-8'))
    return m.hexdigest()

def log(db, doc, changed={}, deleted={}, current_user=None):
    "Create a log entry for the given document."
    entry = dict(_id=get_iuid(),
                 doc=doc['_id'],
                 doctype=doc[constants.DB_DOCTYPE],
                 changed=changed,
                 deleted=deleted,
                 timestamp=timestamp())
    entry[constants.DB_DOCTYPE] = constants.LOG
    try:
        if current_user:
            entry['operator'] = current_user['email']
    except KeyError:
        pass
    db.save(entry)

def cmp_modified(i, j):
    "Compare the two documents by their 'modified' values."
    return cmp(i['timestamp'], j['timestamp'])

def cmp_email(u, v):
    "Compare the two user documents by their 'email' values."
    return cmp(u['email'], v['email'])

def cmp_name(u, v):
    "Compare the two user documents by their 'name' values."
    return cmp(u['name'], v['name'])

def cmp(a, b):
    "Replacement for py2 cmp"
    return (a > b) - (a < b)

class BasePatch(object):
    """Run through all documents in the database and patch the relevant ones.
    Abstract base class."""

    def __init__(self, db):
        self.db = db

    def count_relevant(self):
        "Return the number of documents that match the relevance criterion."
        count = 0
        for key in self.db:
            if self.is_relevant(self.db[key]):
                count += 1
        return count

    def is_relevant(self, doc):
        "Is the relevant document relevant for patch?"
        return constants.DB_DOCTYPE in doc

    def patch_all(self):
        """Run through all documents and patch the relevant ones.
        Return the number of documents patched."""
        count = 0
        for key in self.db:
            doc = self.db[key]
            if self.is_relevant(doc):
                if self.patch_doc(doc):
                    self.db.save(doc)
                    count += 1
        return count

    def patch_doc(self, doc):
        """Patch the document, which is assumed relevant.
        Return True if changed."""
        raise NotImplementedError
