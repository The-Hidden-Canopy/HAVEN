import io

path = "haven/web/server.py"
text = io.open(path, encoding="utf-8").read()

def rep(old, new, count=1):
    global text
    found = text.count(old)
    assert found == count, f"anchor found {found}x (expected {count}): {old[:80]!r}"
    text = text.replace(old, new, count)

# 1. Import the events module.
rep(
    "from ..models import ModelManager, inspect_folder\nfrom ..ipc import IpcDispatcher\n",
    "from ..models import ModelManager, inspect_folder\nfrom ..ipc import IpcDispatcher\nfrom ..ipc.events_pipe import EventPublisher\n",
)

# 2. Method -> event mapping for the native IPC adapter (mutations only).
rep(
    '_AUTHORING_AUTOMATION_ACTION_PATH = re.compile(r"^/api/automations/([^/]+)/(approve|revoke)$")\n',
    '_AUTHORING_AUTOMATION_ACTION_PATH = re.compile(r"^/api/automations/([^/]+)/(approve|revoke)$")\n\n'
    "# Native events pipe (product pass phase 2): mutating IPC methods publish\n"
    "# a domain invalidation on success.  Task/project/claim mutations emit\n"
    "# through the sync listener instead, so web-originated changes notify too.\n"
    "_IPC_METHOD_EVENTS = {\n"
    '    "rooms.add": "home.state.changed",\n'
    '    "rooms.rename": "home.state.changed",\n'
    '    "rooms.remove": "home.state.changed",\n'
    '    "devices.command": "home.state.changed",\n'
    '    "people.add": "home.state.changed",\n'
    '    "people.update": "home.state.changed",\n'
    '    "people.remove": "home.state.changed",\n'
    '    "contexts.add": "home.state.changed",\n'
    '    "contexts.update": "home.state.changed",\n'
    '    "contexts.remove": "home.state.changed",\n'
    '    "automations.create": "home.state.changed",\n'
    '    "automations.update": "home.state.changed",\n'
    '    "automations.enable": "home.state.changed",\n'
    '    "automations.approve": "home.state.changed",\n'
    '    "automations.revoke": "home.state.changed",\n'
    '    "requests.approve": "authority.pending.changed",\n'
    '    "requests.deny": "authority.pending.changed",\n'
    '    "models.download": "models.changed",\n'
    '    "models.install_url": "models.changed",\n'
    '    "models.install_local": "models.changed",\n'
    '    "models.add_endpoint": "models.changed",\n'
    '    "models.add_root": "models.changed",\n'
    '    "models.scan": "models.changed",\n'
    '    "models.register": "models.changed",\n'
    '    "models.load": "models.changed",\n'
    '    "models.unload": "models.changed",\n'
    '    "models.remove": "models.changed",\n'
    '    "models.assign": "models.changed",\n'
    '    "computer.window.focus": "computer.windows.changed",\n'
    '    "computer.observation.set": "computer.activity.changed",\n'
    '    "computer.observation.suppress": "computer.activity.changed",\n'
    '    "computer.action.request": "computer.files.changed",\n'
    '    "computer.action.confirm": "computer.files.changed",\n'
    '    "browser.tab.focus": "browser.tabs.changed",\n'
    '    "browser.tab.open": "browser.tabs.changed",\n'
    '    "browser.tab.close": "browser.tabs.changed",\n'
    '    "browser.tab.close.confirm": "browser.tabs.changed",\n'
    '    "browser.tab.close.deny": "browser.tabs.changed",\n'
    '    "calendar.sources.add": "calendar.changed",\n'
    '    "calendar.sources.remove": "calendar.changed",\n'
    '    "calendar.event.attach": "calendar.changed",\n'
    '    "calendar.event.propose_task": "calendar.changed",\n'
    '    "calendar.event.create": "calendar.changed",\n'
    '    "calendar.event.update": "calendar.changed",\n'
    '    "calendar.event.delete": "calendar.changed",\n'
    '    "calendar.event.confirm": "calendar.changed",\n'
    '    "calendar.event.deny": "calendar.changed",\n'
    '    "email.maildir.set": "email.changed",\n'
    '    "relationships.admit": "relationships.changed",\n'
    '    "relationships.reject": "relationships.changed",\n'
    "}\n",
)

# 3. Publisher + job transition subscription in __init__.
rep(
    "        self.model_jobs = DownloadJobManager(self.models)\n",
    "        self.model_jobs = DownloadJobManager(self.models)\n"
    "        # Native push invalidation (product pass phase 2): one publisher\n"
    "        # feeds the haven-events-<installation-id> pipe.  Every emitter is\n"
    "        # best-effort so a slow or absent client can never stall mutations.\n"
    "        self.events = EventPublisher()\n"
    "        self.model_jobs.subscribe(self._on_model_job_transition)\n",
)

# 4. Extend the sync listener with domain events (fires for web AND native
#    origins, unlike the IPC method wrapper).
rep(
    """            self.sync_engine.record_mutation(
                object_id=str(payload.get(f"{kind}_id") or payload.get("claim_id")),
                kind=kind,
                scope_id=str(payload.get("scope_id")),
                revision=revision,
                payload=payload,
            )
""",
    """            self.sync_engine.record_mutation(
                object_id=str(payload.get(f"{kind}_id") or payload.get("claim_id")),
                kind=kind,
                scope_id=str(payload.get("scope_id")),
                revision=revision,
                payload=payload,
            )
            # Domain invalidations for the native events pipe: keep-last
            # notifications, never row payloads.
            if kind == "task":
                self._emit_event("tasks.changed")
                self._emit_event("relationships.changed")
            elif kind == "project":
                self._emit_event("projects.changed")
                self._emit_event("relationships.changed")
            else:
                self._emit_event("memory.changed")
            self._emit_event("search.index.changed")
""",
)

# 5. Emitter helpers next to the sync appliers.
rep(
    "    def _register_sync_appliers(self) -> None:\n",
    '''    def _emit_event(self, event: str, **data) -> None:
        """Best-effort domain invalidation for native clients (spec 15-17)."""
        publisher = getattr(self, "events", None)
        if publisher is None:
            return
        try:
            publisher.publish(event, **data)
        except Exception:
            # Events must never break the mutation path that produced them.
            pass

    def _on_model_job_transition(self, job) -> None:
        state = getattr(job.state, "value", job.state)
        self._emit_event("model.job.progress", job_id=job.job_id, state=state)

    def _register_sync_appliers(self) -> None:
''',
)

# 6. Wrap mutating handlers before the dispatcher is built.
rep(
    """        return IpcDispatcher(
            {
                "host.capabilities": lambda _params: {
""",
    """        handlers = {
                "host.capabilities": lambda _params: {
""",
)
rep(
    """                "email.maildir.set": _email_maildir_set,
            }
        )
""",
    """                "email.maildir.set": _email_maildir_set,
            }
        }

        def _with_event(event_name: str, handler):
            def _wrapped(params: dict):
                # Emit only on success: exceptions propagate to the dispatcher
                # and must not invalidate a domain that did not change.
                result = handler(params)
                self._emit_event(event_name)
                return result

            return _wrapped

        for _method, _event_name in _IPC_METHOD_EVENTS.items():
            if _method in handlers:
                handlers[_method] = _with_event(_event_name, handlers[_method])
        return IpcDispatcher(handlers)
""",
)

# 7. Stop the publisher with the server.
rep(
    """    def server_close(self) -> None:
        self.director.stop_scheduler()
""",
    """    def server_close(self) -> None:
        try:
            self.events.stop()
        except Exception:
            pass
        self.director.stop_scheduler()
""",
)

io.open(path, "w", encoding="utf-8", newline="").write(text)
print("server.py event emitters wired")
