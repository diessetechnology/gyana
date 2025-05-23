from datetime import timedelta
from itertools import chain

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property

from apps.base.models import BaseModel
from apps.base.tables import ICONS
from apps.nodes.models import Node
from apps.projects.models import Project
from apps.runs.models import JobRun
from apps.users.models import CustomUser
from apps.widgets.models import Widget

from .clone import clone_connector_and_tables

PENDING_DELETE_AFTER_DAYS = 30


class IntegrationsManager(models.Manager):
    def visible(self):
        # cleanup up periodically
        return self.exclude(
            created__lt=timezone.now() - timedelta(days=PENDING_DELETE_AFTER_DAYS),
            ready=False,
        )

    def pending(self):
        return self.visible().filter(ready=False)

    def ready(self):
        return self.visible().filter(ready=True)

    def needs_attention(self):
        return self.pending().exclude(state=Integration.State.LOAD)

    def loading(self):
        return self.pending().filter(state=Integration.State.LOAD)

    def sheets(self):
        return self.ready().filter(kind=Integration.Kind.SHEET)

    def uploads(self):
        return self.ready().filter(kind=Integration.Kind.UPLOAD)

    def review(self):
        return self.pending().filter(state=Integration.State.DONE)

    def pending_should_be_deleted(self):
        return self.filter(
            ready=False,
            created__lt=timezone.now() - timedelta(days=PENDING_DELETE_AFTER_DAYS),
        )


class Integration(BaseModel):
    class Kind(models.TextChoices):
        SHEET = "sheet", "Sheet"
        UPLOAD = "upload", "Upload"
        CUSTOMAPI = "customapi", "Custom API"

    class State(models.TextChoices):
        UPDATE = "update", "Update"
        PENDING = "pending", "Pending"
        LOAD = "load", "Load"
        ERROR = "error", "Error"
        DONE = "done", "Done"

    _clone_excluded_m2o_or_o2m_fields = ["runs", "table_set"]

    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    kind = models.CharField(max_length=32, choices=Kind.choices)

    # user editable name, auto-populated in the initial sync
    name = models.CharField(max_length=255)

    state = models.CharField(max_length=16, choices=State.choices, default=State.UPDATE)
    # only "ready" are available for analytics and count towards user rows
    ready = models.BooleanField(default=False)
    created_ready = models.DateTimeField(null=True)
    created_by = models.ForeignKey(CustomUser, null=True, on_delete=models.SET_NULL)

    is_scheduled = models.BooleanField(default=False)

    objects = IntegrationsManager()

    STATE_TO_ICON = {
        State.UPDATE: ICONS["warning"],
        State.PENDING: ICONS["pending"],
        State.LOAD: ICONS["loading"],
        State.ERROR: ICONS["error"],
        State.DONE: ICONS["info"],
    }

    STATE_TO_MESSAGE = {
        State.UPDATE: "Incomplete setup",
        State.PENDING: "Pending",
        State.LOAD: "Importing",
        State.ERROR: "Error",
        State.DONE: "Ready to review",
    }

    RUN_STATE_TO_INTEGRATION_STATE = {
        JobRun.State.PENDING: State.PENDING,
        JobRun.State.RUNNING: State.LOAD,
        JobRun.State.FAILED: State.ERROR,
        JobRun.State.SUCCESS: State.DONE,
    }

    KIND_RUN_IN_PROJECT = [Kind.SHEET, Kind.CUSTOMAPI]

    def __str__(self):
        return self.name

    @property
    def state_icon(self):
        return (
            ICONS["success"]
            if self.ready and self.state == self.State.DONE
            else self.STATE_TO_ICON[self.state]
        )

    @property
    def state_text(self):
        return (
            "Success"
            if self.ready and self.state == self.State.DONE
            else self.STATE_TO_MESSAGE[self.state]
        )

    @property
    def source_obj(self):
        return getattr(self, self.kind)

    @cached_property
    def num_rows(self):
        return (
            self.table_set.all().aggregate(models.Sum("num_rows"))["num_rows__sum"] or 0
        )

    @property
    def num_tables(self):
        return self.table_set.count()

    @property
    def last_synced(self):
        if self.kind == self.Kind.SHEET:
            return self.sheet.drive_file_last_modified_at_sync
        return self.created_ready

    @property
    def expires(self):
        if self.ready:
            return
        return max(
            timezone.now(), self.created + timedelta(days=PENDING_DELETE_AFTER_DAYS)
        )

    @property
    def used_in_nodes(self):
        return (
            Node.objects.filter(
                kind=Node.Kind.INPUT, input_table__in=self.table_set.all()
            )
            .distinct("workflow", "input_table")
            .annotate(
                parent_kind=models.Value("Workflow", output_field=models.CharField())
            )
        )

    @property
    def used_in_widgets(self):
        return (
            Widget.objects.filter(table__in=self.table_set.all())
            .distinct("page__dashboard", "table")
            .annotate(
                parent_kind=models.Value("Dashboard", output_field=models.CharField())
            )
        )

    @property
    def used_in(self):
        return list(chain(self.used_in_nodes, self.used_in_widgets))

    def get_table_name(self):
        return f"Integration:{self.name}"

    def get_absolute_url(self):
        from apps.integrations.mixins import STATE_TO_URL_REDIRECT

        if not self.ready:
            url_name = STATE_TO_URL_REDIRECT[self.state]
            return reverse(url_name, args=(self.project.id, self.id))

        return reverse("project_integrations:detail", args=(self.project.id, self.id))

    @property
    def icon(self):
        return f"images/integrations/{self.kind}.svg"

    def get_table_by_pk_safe(self, table_pk):
        from apps.tables.models import Table

        # none or empty string
        if not table_pk:
            return self.table_set.order_by("name").first()
        try:
            return self.table_set.get(pk=table_pk)
        except (Table.DoesNotExist, ValueError):
            return self.table_set.order_by("name").first()

    @property
    def latest_run(self):
        return self.runs.order_by("-created").first()

    def update_state_from_latest_run(self):
        self.state = (
            self.State.UPDATE
            if not self.latest_run
            else self.RUN_STATE_TO_INTEGRATION_STATE[self.latest_run.state]
        )
        self.save()

    def make_clone(self, attrs=None, sub_clone=False, using=None):
        clone = super().make_clone(attrs=attrs, sub_clone=sub_clone, using=using)
        clone_connector_and_tables(self, clone, using)
        return clone
