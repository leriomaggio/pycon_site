# -*- coding: UTF-8 -*-
from __future__ import absolute_import
import re
from django import template
from django.conf import settings

from p3 import models

register = template.Library()

class LatestNewsNode(template.Node):
    def __init__(self, limit, var_name):
        self.limit = limit
        self.var_name = var_name
    def render(self, context):
        query = models.Deadline.objects.valid_news()
        if self.limit:
            query = query[:self.limit]
        lang = context.get('LANGUAGE_CODE', settings.LANGUAGES[0][0])
        news = []
        for n in query:
            contents = n.deadlinecontent_set.get(language = lang)
            news.append((n.date, contents.body))
        context[self.var_name] = news
        return ""

@register.tag
def latest_news(parser, token):
    contents = token.split_contents()
    tag_name = contents[0]
    try:
        limit = int(contents[1])
    except IndexError:
        limit = None
    except (ValueError, TypeError):
        raise template.TemplateSyntaxError("%r tag's argument should be an integer" % tag_name)
    if contents[-2] != 'as':
        raise template.TemplateSyntaxError("%r tag had invalid arguments" % tag_name)
    var_name = contents[-1]
    return LatestNewsNode(limit, var_name)
