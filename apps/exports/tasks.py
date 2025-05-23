import uuid

import analytics
from celery.app import shared_task
from django.conf import settings
from django.utils import timezone

from apps.base.analytics import EXPORT_CREATED
from apps.base.clients import get_engine
from apps.exports.emails import send_export_email
from apps.nodes.engine import get_query_from_node
from apps.users.models import CustomUser

from .models import Export


@shared_task
def run_export_task(export_id, user_id):
    export = Export.objects.get(pk=export_id)
    user = CustomUser.objects.get(pk=user_id)
    engine = get_engine()
    if export.node:
        query = get_query_from_node(export.node)
    else:
        query = engine.get_table(export.integration_table)

    path = f"exports/export_{uuid.uuid4()}.csv"
    export.file.name = path
    engine.export_to_csv(query, export)

    send_export_email(export, user)

    export.exported_at = timezone.now()
    export.save()

    analytics.track(
        user.id,
        EXPORT_CREATED,
        {
            "id": export.id,
        },
    )
