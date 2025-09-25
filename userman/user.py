" Userman: User handlers. "

import logging

import tornado.web
import pycountry

from . import constants
from . import settings
from . import utils
from .saver import DocumentSaver
from .requesthandler import RequestHandler


class UserSaver(DocumentSaver):

    doctype = constants.USER

    def initialize(self):
        self['status'] = constants.PENDING
        self['services'] = []
        self['teams'] = []
        self['created'] = utils.timestamp()

    def check_email(self, value):
        """Raise ValueError if given email value has wrong format.
        Raise KeyError if the value conflicts with another."""
        if not value:
            raise ValueError('email must be a non-empty value')
        if value == self.doc.get('email'): return
        if '/' in value:
            raise ValueError("slash '/' disallowed in email")
        parts = value.split('@')
        if len(parts) != 2:
            raise ValueError("at-sign '@' not used correcly in email")
        if len(parts[1].split('.')) < 2:
            raise ValueError('invalid domain name part in email')
        if len(list(self.db.view('user/email', key=value))) > 0:
            raise KeyError("email already in use")

    def check_username(self, value):
        """Raise ValueError if the given username has wrong format.
        Raise KeyError if the value conflicts with another."""
        if not value: return
        if value == self.doc.get('username'): return
        if '/' in value:
            raise ValueError("slash '/' disallowed in username")
        if '@' in value:
            raise ValueError("at-sign '@' disallowed in username")
        if len(list(self.db.view('user/username', key=value))) > 0:
            raise KeyError('username already in use')

    def convert_email(self, value):
        "Convert email value to lower case."
        return value.lower()

    def check_password(self, value):
        "Check password quality."
        utils.check_password_quality(value)

    def convert_password(self, value):
        return utils.hashed_password(value)

    def check_status(self, value):
        "Check status value."
        if value not in constants.STATUSES:
            raise ValueError('invalid status value')


class UserMixin(object):

    def may_access_user(self, user):
        if not self.current_user: return False
        if self.is_admin(): return True
        if user['email'] == self.current_user['email']: return True
        return False

    def check_access_user(self, user):
        if not self.may_access_user(user):
            raise tornado.web.HTTPError(403, 'you may not access user')


class User(UserMixin, RequestHandler):
    "Display a user account."

    @tornado.web.authenticated
    def get(self, email):
        user = self.get_user(email)
        self.check_access_user(user)
        services = [self.get_service(n) for n in user['services']]
        teams = [self.get_team(n) for n in user['teams']]
        self.render('user.html',
                    user=user,
                    services=services,
                    teams=teams,
                    logs=self.get_logs(user['_id']))


class UserEdit(UserMixin, RequestHandler):
    "Edit a user account."

    @tornado.web.authenticated
    def get(self, email):
        user = self.get_user(email)
        self.check_access_user(user)
        teams = [self.get_team(n) for n in user['teams']]
        leading = [t for t in teams if email in t['leaders']]
        self.render('user_edit.html',
                    user=user,
                    services=self.get_all_services(),
                    teams=teams,
                    leading=leading,
                    countries=sorted([c.name for c in pycountry.countries]))

    @tornado.web.authenticated
    def post(self, email):
        self.check_xsrf_cookie()
        user = self.get_user(email)
        self.check_access_user(user)
        with UserSaver(doc=user, rqh=self) as saver:
            if self.is_admin():
                role = self.get_argument('role', None)
                if role in constants.ROLES:
                    saver['role'] = role
                saver['services'] = self.get_arguments('service')
            saver['username'] = self.get_argument('username', None)
            saver['name'] = self.get_argument('name')
            saver['department'] = self.get_argument('department', None)
            saver['university'] = self.get_argument('university', None)
            saver['country'] = self.get_argument('country')
        self.redirect(self.reverse_url('user', user['email']))


class UserApproveMixin(object):
    "Mixin to factor out common approval code."

    def approve_user(self, user):
        "Approve the given user."
        assert self.is_admin()
        with UserSaver(doc=user, rqh=self) as saver:
            activation_code = utils.get_iuid()
            deadline = utils.timestamp(days=settings['ACTIVATION_PERIOD'])
            saver['activation'] = dict(code=activation_code, deadline=deadline)
            saver['status'] = constants.APPROVED
        url = self.get_absolute_url('user_activate')
        url_with_params = self.get_absolute_url('user_activate',
                                                email=user['email'],
                                                activation_code=activation_code)
        text = open(settings['ACTIVATION_EMAIL']).read().format(
            period=settings['ACTIVATION_PERIOD'],
            url=url,
            url_with_params=url_with_params,
            email=user['email'],
            activation_code=activation_code)
        self.send_email(user,
                        self.current_user,
                        'Userman account activation',
                        text)
        self.redirect(self.reverse_url('user', user['email']))


class UserCreate(UserApproveMixin, RequestHandler):
    """Create a user account. Anyone may do this.
    If non-admin, then send an email to the admin requesting approval review.
    If admin, approve immediately and send that email instead."""

    def get(self):
        "Display the user account creation form."
        self.render('user_create.html',
                    countries=sorted([c.name for c in pycountry.countries]))

    def post(self):
        "Create the user account."
        self.check_xsrf_cookie()
        # Some fields initialized by UserSaver
        with UserSaver(rqh=self) as saver:
            saver['email'] = self.get_argument('email')
            saver['username'] = self.get_argument('username', None)
            saver['role'] = constants.USER
            saver['name'] = self.get_argument('name')
            saver['department'] = self.get_argument('department', None)
            saver['university'] = self.get_argument('university', None)
            saver['country'] = self.get_argument('country')
            saver['services'] = [r['key'] for r in self.db.view('service/public')]
            user = saver.doc
        if self.is_admin(): # Activate immediately if admin creator is admin.
            self.approve_user(user)
        else:               # Require approval by admin if non-admin creator.
            text = "Review Userman account {email} for approval: {url}".format(
                email=user['email'],
                url=self.get_absolute_url('user', user['email']))
            for admin in self.get_admins():
                self.send_email(admin,
                                admin,
                                'Review Userman account for approval',
                                text)
                url = self.reverse_url('user_acknowledge', user['email'])
                self.redirect(url)


class UserAcknowledge(RequestHandler):
    """Acknowledge the creation of the user account.
    Explain what is going to happen."""

    def get(self, name):
        user = self.get_user(name)
        if user['status'] != constants.PENDING:
            raise tornado.web.HTTPError(409, 'account not pending')
        self.render('user_acknowledge.html', user=user)


class UserApprove(UserApproveMixin, RequestHandler):
    "Approve a user account; email the activation code."

    @tornado.web.authenticated
    def post(self, name):
        self.check_xsrf_cookie()
        self.check_admin()
        user = self.get_user(name)
        if user['status'] != constants.PENDING:
            raise tornado.web.HTTPError(409, 'account not pending')
        self.approve_user(user)


class UserBlock(RequestHandler):
    "Block a user account."

    @tornado.web.authenticated
    def post(self, name):
        self.check_xsrf_cookie()
        self.check_admin()
        user = self.get_user(name)
        if user['status'] != constants.BLOCKED:
            if user['role'] == constants.ADMIN:
                raise tornado.web.HTTPError(409, 'cannot block admin account')
            with UserSaver(doc=user, rqh=self) as saver:
                saver['status'] = constants.BLOCKED
        self.redirect(self.reverse_url('user', user['email']))


class UserUnblock(RequestHandler):
    "Unblock a user account."

    @tornado.web.authenticated
    def post(self, name):
        self.check_xsrf_cookie()
        self.check_admin()
        user = self.get_user(name)
        if user['status'] != constants.ACTIVE:
            with UserSaver(doc=user, rqh=self) as saver:
                saver['status'] = constants.ACTIVE
        self.redirect(self.reverse_url('user', user['email']))


class UserActivate(RequestHandler):
    "Activate the user account, setting the password."

    def get(self):
        self.render('user_activate.html',
                    email=self.get_argument('email', ''),
                    activation_code=self.get_argument('activation_code', ''))

    def post(self):
        self.check_xsrf_cookie()
        email = self.get_argument('email', None)
        activation_code = self.get_argument('activation_code', None)
        try:
            if not email:
                raise ValueError('missing email')
            if not activation_code:
                raise ValueError('missing activation code')
            password = self.get_argument('password', '')
            utils.check_password_quality(password)
            confirm_password = self.get_argument('confirm_password', '')
            if password != confirm_password:
                raise ValueError('passwords do not match')
            message = 'no such user, or invalid or expired activation code'
            try:
                user = self.get_user(email)
            except:
                raise ValueError(message)
            activation = user.get('activation', dict())
            if activation.get('code') != activation_code:
                raise ValueError(message)
            if activation.get('deadline', '') < utils.timestamp():
                raise ValueError(message)
            with UserSaver(doc=user, rqh=self) as saver:
                del saver['activation']
                saver['password'] = password
                saver['status'] = constants.ACTIVE
            self.set_secure_cookie(constants.USER_COOKIE_NAME, email)
            self.redirect(self.reverse_url('user', email))
        except ValueError as msg:
            self.render('user_activate.html',
                        error=str(msg),
                        email=email,
                        activation_code=activation_code)


class UserReset(RequestHandler):
    "Reset the user password, sending out an activation code."

    def get(self):
        self.render('user_reset.html')

    def post(self):
        self.check_xsrf_cookie()
        try:
            user = self.get_user(self.get_argument('email'))
            if user.get('status') not in (constants.APPROVED, constants.ACTIVE):
                raise ValueError('account status not active')
            with UserSaver(doc=user, rqh=self) as saver:
                activation_code = utils.get_iuid()
                deadline = utils.timestamp(days=settings['ACTIVATION_PERIOD'])
                saver['activation'] = dict(code=activation_code, deadline=deadline)
                saver['password'] = utils.get_iuid()
                saver['status'] = constants.APPROVED
            url = self.get_absolute_url('user_activate')
            url_with_params = self.get_absolute_url('user_activate',
                                                    email=user['email'],
                                                    activation_code=activation_code)
            text = open(settings['RESET_EMAIL']).read().format(
                period=settings['ACTIVATION_PERIOD'],
                url=url,
                url_with_params=url_with_params,
                email=user['email'],
                activation_code=activation_code)
            self.send_email(user,
                            self.get_admins()[0], # Arbitrarily the first admin
                            'Userman account password reset',
                            text)
        except (tornado.web.HTTPError, ValueError) as msg:
            logging.debug("account reset error: %s", msg)
        self.redirect(self.reverse_url('home'))


class Users(RequestHandler):
    "List of all user accounts."

    @tornado.web.authenticated
    def get(self):
        self.check_admin()
        users = [r['doc'] for r in self.db.view('user/email', include_docs=True)]
        self.render('users.html', users=users)


class UsersPending(RequestHandler):
    "List of pending user accounts."

    @tornado.web.authenticated
    def get(self):
        self.check_admin()
        users = [r['doc'] for r in self.db.view('user/pending', include_docs=True)]
        self.render('users_pending.html', users=users)


class UsersBlocked(RequestHandler):
    "List of blocked user accounts."

    @tornado.web.authenticated
    def get(self):
        self.check_admin()
        users = [r['doc'] for r in self.db.view('user/blocked', include_docs=True)]
        self.render('users_blocked.html', users=users)
