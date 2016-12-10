#!/usr/bin/env python

"""
conference.py -- server-side Python App Engine API;
    uses Google Cloud Endpoints

"""


from datetime import datetime
import logging

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Alert
from models import LatestAlert

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api( name='conference',
                version='v1',
                allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
                scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -
    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        # both for data model & outbound Message
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
            setattr(request, "seatsAvailable", data["maxAttendees"])

        # make Profile Key from user ID
        p_key = ndb.Key(Profile, user_id)
        # allocate new Conference ID with Profile key as parent
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        # make Conference key from ID
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference & return (modified) ConferenceForm
        Conference(**data).put()

        return request

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                path='queryConferences',
                http_method='POST',
                name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

         # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") \
            for conf in conferences]
        )


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # make profile key
        p_key = ndb.Key(Profile, getUserId(user))
        # create ancestor query for this user
        conferences = Conference.query(ancestor=p_key)
        # get the user profile and display name
        prof = p_key.get()
        displayName = getattr(prof, 'displayName')
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, displayName) for conf in conferences]
        )


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        logging.debug("Did run_copyProfileToForm()")
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        ## TODO
        ## make sure user is authed
        ## uncomment the following lines:
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        # get Profile from datastore
        user_id = getUserId(user) # id is the email
        p_key = ndb.Key(Profile, user_id) # generate key
        profile = p_key.get()
        print '-*|_'*50
        print dir(profile)
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        q = Conference.query()
        # simple filter usage:
        q = q.filter(Conference.city == "London")

        # advanced filter building and usage
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)

        # TODO
        # add 2 filters:
        # 1: city equals to Chicago
        # 2: topic equals "Medical Innovations"

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

# - - - Alerts - - - - - - - - - - - - - - - - - - - - - - -

    def _doAlert(self):
        """Get latest alert and return to user"""
        # retrieve latest alert from datastore
        a = Alert.query().order(-Alert.date).get()
        la = LatestAlert()
        for field in la.all_fields():
            # retrieve content string and discard the rest
            if field.name == 'content':
                setattr(la, field.name, a.content)
        la.check_initialized()
        return la


    @endpoints.method(message_types.VoidMessage, LatestAlert,
                path='alert', http_method='GET', name='getAlert')
    def getAlert(self, request):
        """Return latest alert."""
        return self._doAlert()


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        q = Conference.query()
        # simple filter usage:
        # q = q.filter(Conference.city == "Paris")

        # advanced filter building and usage
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)

        # TODO
        # add 2 filters:
        # 1: city equals to Chicago
        # 2: topic equals "Medical Innovations"
        q = q.filter(Conference.city=="Chicago")
        q = q.filter(Conference.topics=="Medical Innovations")

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='poplulate', http_method='GET', name='populateApp')
    def populateApp(self, request):
        """Populate and reutrn conference"""

        confs = [ConferenceForm( name= u'aa', description= u'Uno', topics= [u'Medical Innovations'], city= u'Chicago',
                  maxAttendees= 10, startDate= u'2016-11-25T05:00:00.000Z', endDate= u'2016-12-03T05:00:00.000Z'),

                ConferenceForm( name= u'bb', description= u'Dos', topics= [u'Programming Languages'], city= u'London',
                  maxAttendees= 20, startDate= u'2016-10-15T05:00:00.000Z', endDate= u'2016-10-03T05:00:00.000Z'),

                ConferenceForm( name= u'cc', description= u'Tres', topics= [u'Web Technologies'], city= u'Paris',
                  maxAttendees= 30, startDate= u'2016-10-15T05:00:00.000Z', endDate= u'2016-10-25T05:00:00.000Z'),

                ConferenceForm( name= u'dd', description= u'Cuatro', topics= [u'Movie Making'], city= u'Tokyo',
                  maxAttendees= 40, startDate= u'2016-10-31T05:00:00.000Z', endDate= u'2016-11-05T05:00:00.000Z'),

                ConferenceForm( name= u'ee', description= u'Cinco', topics= [u'Health and Nutrition'], city= u'Chicago',
                  maxAttendees= 50, startDate= u'2016-11-15T05:00:00.000Z', endDate= u'2016-12-03T05:00:00.000Z'),

                ConferenceForm( name= u'ff', description= u'Seis', topics= [u'Medical Innovations', u'Movie Making'], city= u'London',
                  maxAttendees= 60, startDate= u'2016-11-28T05:00:00.000Z', endDate= u'2016-12-05T05:00:00.000Z'),

                ConferenceForm( name= u'gg', description= u'Siete', topics= [u'Health and Nutrition', u'Programming Languages'], city= u'Paris',
                  maxAttendees= 70, startDate= u'2016-12-08T05:00:00.000Z', endDate= u'2016-12-25T05:00:00.000Z')
               ]

        for conf in confs:
            self._createConferenceObject(conf)

        conferences = Conference.query()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, None) for conf in conferences]
        )

# registers API
api = endpoints.api_server([ConferenceApi])
