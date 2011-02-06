# -*- coding: UTF-8 -*-
from django import forms
from django import http
from django.conf import settings as dsettings
from django.contrib import auth
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.forms.formsets import BaseFormSet, formset_factory
from django.db import transaction
from django.shortcuts import redirect, render_to_response
from django.template import RequestContext
from django.template.defaultfilters import slugify
from django.views.decorators.csrf import csrf_exempt

from assopy import forms as aforms
from assopy import janrain
from assopy import models
from assopy import settings

from conference import models as cmodels

import logging
from itertools import izip_longest

log = logging.getLogger('assopy.views')

class HttpResponseRedirectSeeOther(http.HttpResponseRedirect):
    status_code = 303

# see: http://www.djangosnippets.org/snippets/821/
def render_to(template):
    """
    Decorator for Django views that sends returned dict to render_to_response function
    with given template and RequestContext as context instance.

    If view doesn't return dict then decorator simply returns output.
    Additionally view can return two-tuple, which must contain dict as first
    element and string with template name as second. This string will
    override template name, given as parameter

    Parameters:

     - template: template name to use
    """
    def renderer(func):
        def wrapper(request, *args, **kw):
            output = func(request, *args, **kw)
            if isinstance(output, (list, tuple)):
                return render_to_response(output[1], output[0], RequestContext(request))
            elif isinstance(output, dict):
                return render_to_response(template, output, RequestContext(request))
            return output
        return wrapper
    return renderer

@login_required
@render_to('assopy/home.html')
def home(request):
    user = request.user.assopy_user

    initial = user.billing()
    form_profile = aforms.Profile(initial=initial)
    form_billing = aforms.BillingData(initial=initial)

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action not in ('profile', 'billing'):
            return http.HttpResponseBadRequest()
        if action == 'profile':
            form_profile = aforms.Profile(data=request.POST, files=request.FILES)
            if form_profile.is_valid():
                data = form_profile.cleaned_data
                if data['photo']:
                    user.photo = data['photo']
                    user.save()
                return HttpResponseRedirectSeeOther('.')
    return {
        'user': user,
        'form_profile': form_profile,
        'form_billing': form_billing,
    }

@login_required
@render_to('assopy/profile.html')
def profile(request):
    user = request.user.assopy_user
    if request.method == 'POST':
        form = aforms.Profile(data=request.POST, files=request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            if data['photo']:
                user.photo = data['photo']
                user.save()
            del data['photo']
            user.setBilling(**data)
            return HttpResponseRedirectSeeOther('.')
    else:
        initial = user.billing()
        form = aforms.Profile(initial=initial)
    return {
        'user': user,
        'form': form,
    }

@render_to('assopy/new_account.html')
def new_account(request):
    next = request.GET.get('next', reverse('assopy-home'))
    if not next.startswith('/'):
        next = reverse('assopy-home')
    if request.user.is_authenticated():
        return redirect(next)

    if request.method == 'GET':
        form = aforms.NewAccountForm()
    else:
        form = aforms.NewAccountForm(data=request.POST)
        if form.is_valid():
            data = form.cleaned_data
            log.info('new account for "%s %s" (%s) created', data['first_name'], data['last_name'], data['email'])
            return redirect(next)
    return {
        'form': form,
        'next': next,
    }

@login_required
@render_to('assopy/speaker.html')
@transaction.commit_on_success
def speaker(request):
    user = request.user.assopy_user
    speaker = user.speaker

    class BaseTalkFormSet(BaseFormSet):
        def add_fields(self, form, index):
            super(BaseTalkFormSet, self).add_fields(form, index)
            form.fields['id'] = forms.IntegerField(required=False, widget=forms.HiddenInput())

    TalkFormSet = formset_factory(aforms.Talk, formset=BaseTalkFormSet)
    if speaker is None:
        initial = {}
        talks = []
    else:
        initial = {'bio': getattr(speaker.getBio(), 'body', '')}
        talks = speaker.talk_set.all()

    form_speaker = aforms.Speaker(initial=initial)
    formset_talk = TalkFormSet(initial=[
        {
            'id': t.id,
            'title': t.title,
            'duration': t.duration,
            'language': t.language,
            'abstract': getattr(t.getAbstract(), 'body', ''),
            'slides': t.slides,
        } for t in talks
    ])
    if request.method == 'POST':
        action = request.POST.get('action')
        if action not in ('talks', 'speaker', 'delete-talk'):
            return http.HttpResponseBadRequest()
        if speaker is None:
            speaker = cmodels.Speaker()
            speaker.name= user.name()
            speaker.slug = slugify(speaker.name)
            speaker.save()
            user.speaker = speaker
            user.save()
            
        if action == 'talks':
            formset_talk = TalkFormSet(data=request.POST, files=request.FILES)
            if formset_talk.is_valid():
                data = formset_talk.cleaned_data
                for d, t in izip_longest(data, talks):
                    if not d:
                        continue
                    new = t is None
                    if new:
                        t = cmodels.Talk()
                    t.title = d['title']
                    t.duration = d['duration']
                    t.language = d['language']
                    t.slides = d['slides']
                    t.save()
                    t.setAbstract(d['abstract'])
                    if new:
                        t.speakers.add(speaker)
                return HttpResponseRedirectSeeOther('.')
        elif action == 'speaker':
            form_speaker = aforms.Speaker(data=request.POST)
            if form_speaker.is_valid():
                data = form_speaker.cleaned_data
                speaker.setBio(data['bio'])
                speaker.save()
                return HttpResponseRedirectSeeOther('.')
        elif action == 'delete-talk':
            tid = int(request.POST['delete_id'])
            for t in talks:
                if t.id == tid:
                    t.delete()
                    break
            return HttpResponseRedirectSeeOther('.')

    return {
        'user': user,
        'form_speaker': form_speaker,
        'formset_talk': formset_talk,
    }

@csrf_exempt
@transaction.commit_on_success
def janrain_token(request):
    if request.method != 'POST':
        return http.HttpResponseNotAllowed(('POST',))
    redirect_to = request.session.get('jr_next', '/')
    try:
        token = request.POST['token']
    except KeyError:
        return http.HttpResponseBadRequest()
    profile = janrain.auth_info(settings.JANRAIN['secret'], token)
    log.info('janrain profile: %s', profile['identifier'])

    current = request.user
    duser = auth.authenticate(identifier=profile['identifier'])
    if duser is None:
        log.info('%s is a new identity', profile['identifier'])
        # è la prima volta che questo utente si logga con questo provider
        if not current.is_anonymous():
            # l'utente corrente non è anonimo, recupero il suo utente assopy
            # per associarci la nuova identità
            try:
                user = current.assopy_user
            except models.User.DoesNotExist:
                # può accadere che l'utente corrente non sia un utente assopy
                log.debug('the current user "%s" will become an assopy user', current)
                user = models.User(user=current)
                user.save()
        else:
            # devo creare tutto, utente django, assopy e identità
            current = auth.models.User.objects.create_user(janrain.suggest_username(profile), profile.get('email'))
            try:
                current.first_name = profile['name']['givenName']
            except KeyError:
                pass
            try:
                current.last_name = profile['name']['familyName']
            except KeyError:
                pass
            current.save()
            log.debug('new django user created "%s"', current)
            user = models.User(user=current)
            user.save()
        log.debug('the new identity will be linked to "%s"', current)
        identity = models.UserIdentity.objects.create_from_profile(user, profile)

        duser = auth.authenticate(identifier=identity.identifier)
        auth.login(request, duser)
    else:
        # è un utente conosciuto, devo solo verificare che lo user associato
        # all'identità e quello loggato in questo momento siano la stessa
        # persona
        if current.is_anonymous():
            # ok, non devo fare altro che loggarmi con l'utente collegato
            # all'identità
            auth.login(request, duser)
        elif current != duser:
            # l'utente corrente e quello collegato all'identità non coincidano
            # devo mostrare un messaggio di errore
            return HttpResponseRedirectSeeOther(reverse('assopy-janrain-login_mismatch'))
        else:
            # non ho niente da fare, l'utente è già loggato
            pass
    return HttpResponseRedirectSeeOther(redirect_to)

@render_to('assopy/janrain_login_mismatch.html')
def janrain_login_mismatch(request):
    return {}