using System.Buffers.Binary;
using System.IO.Pipes;
using System.Text.Json;

namespace Haven.Desktop;

public sealed class HavenCoreClient : IAsyncDisposable
{
    private const string ProtocolVersion = "haven-ipc-1";
    private const int MaxFrameBytes = 1 << 20;
    private const string PipePrefix = "\\\\.\\pipe\\";
    private readonly string _pipeName;
    private readonly string _authToken;
    private readonly JsonSerializerOptions _json = new() { PropertyNameCaseInsensitive = true };
    private readonly SemaphoreSlim _requestLock = new(1, 1);
    private NamedPipeClientStream? _pipe;

    public HavenCoreClient(string pipeName, string authToken)
    {
        _pipeName = pipeName.StartsWith(PipePrefix, StringComparison.OrdinalIgnoreCase)
            ? pipeName[PipePrefix.Length..]
            : pipeName;
        _authToken = authToken;
    }

    public async Task ConnectAsync(CancellationToken cancellationToken = default)
    {
        if (_pipe is not null && _pipe.IsConnected)
        {
            return;
        }
        _pipe = new NamedPipeClientStream(".", _pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        await _pipe.ConnectAsync(5000, cancellationToken);
        using var response = await RequestDocumentAsync(
            "host.authenticate",
            new { token = _authToken },
            cancellationToken);
        EnsureSuccess(response);
    }

    public async Task DisconnectAsync()
    {
        // Reconnect lifecycle: dispose the dead pipe before reconnecting so
        // no stale handle survives into the next attempt.
        var pipe = _pipe;
        _pipe = null;
        if (pipe is not null)
        {
            try
            {
                await pipe.DisposeAsync();
            }
            catch (Exception)
            {
                // A broken pipe disposes best-effort.
            }
        }
    }

    public Task<JsonElement> GetStateAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("state.get", new { }, cancellationToken);

    public Task<JsonElement> SearchAsync(string text, CancellationToken cancellationToken = default) =>
        RequestResultAsync("search.query", new { text, limit = 50 }, cancellationToken);

    public Task<JsonElement> GetKnowledgeClaimsAsync(
        bool includeStale = false,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("knowledge.claims", new { include_stale = includeStale }, cancellationToken);

    public Task<JsonElement> GetKnowledgeClaimAsync(
        string claimId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("knowledge.claim", new { claim_id = claimId }, cancellationToken);

    public Task<JsonElement> CorrectKnowledgeClaimAsync(
        string claimId,
        string proposition,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "knowledge.claim.correct",
            new { claim_id = claimId, proposition },
            cancellationToken);

    public Task<JsonElement> MarkKnowledgeClaimStaleAsync(
        string claimId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("knowledge.claim.stale", new { claim_id = claimId }, cancellationToken);

    public Task<JsonElement> ForgetKnowledgeClaimAsync(
        string claimId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("knowledge.claim.forget", new { claim_id = claimId }, cancellationToken);

    public Task<JsonElement> AskAsync(string text, CancellationToken cancellationToken = default) =>
        RequestResultAsync("composer.ask", new { text }, cancellationToken);

    public Task<JsonElement> GetSetupStatusAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.status", new { }, cancellationToken);

    public Task<JsonElement> ChooseSetupDataDirAsync(string path, CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.data_dir", new { path }, cancellationToken);

    public Task<JsonElement> ConnectSetupProviderAsync(
        string baseUrl,
        string token,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "setup.provider.connect",
            new { kind = "home_assistant", base_url = baseUrl, token },
            cancellationToken);

    public Task<JsonElement> SkipSetupProviderAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.provider.connect", new { skip = true }, cancellationToken);

    public Task<JsonElement> ScanSetupDiscoveryAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.discovery.scan", new { }, cancellationToken);

    public Task<JsonElement> EnrollSetupCandidateAsync(
        string candidateId,
        string deviceType,
        string? room = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "setup.enroll",
            new { candidate_id = candidateId, device_type = deviceType, room },
            cancellationToken);

    public Task<JsonElement> AddSetupPersonAsync(
        string name,
        string role = "member",
        string? entityId = null,
        string? roomId = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "setup.household.people.add",
            new { name, role, entity_id = entityId, room_id = roomId },
            cancellationToken);

    public Task<JsonElement> RemoveSetupPersonAsync(
        string personId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.household.people.remove", new { person_id = personId }, cancellationToken);

    public Task<JsonElement> AddSetupContextAsync(
        string label,
        string entityId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "setup.household.contexts.add",
            new { label, entity_id = entityId },
            cancellationToken);

    public Task<JsonElement> RemoveSetupContextAsync(
        string contextId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "setup.household.contexts.remove",
            new { context_id = contextId },
            cancellationToken);

    public Task<JsonElement> SetSetupPreferencesAsync(
        bool voice,
        bool intelligence,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.preferences", new { voice, intelligence }, cancellationToken);

    public Task<JsonElement> SetSetupComputerAsync(
        bool enabled,
        bool? readOnly = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.computer", new { enabled, read_only = readOnly }, cancellationToken);

    public Task<JsonElement> AddSetupComputerRootAsync(
        string path,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.computer.roots.add", new { path }, cancellationToken);

    public Task<JsonElement> RemoveSetupComputerRootAsync(
        string path,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.computer.roots.remove", new { path }, cancellationToken);

    public Task<JsonElement> ScanSetupComputerAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.computer.scan", new { }, cancellationToken);

    public Task<JsonElement> CompleteSetupAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.complete", new { }, cancellationToken);

    public Task<JsonElement> ReopenSetupAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("setup.reopen", new { }, cancellationToken);

    public Task<JsonElement> GetRoomsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("rooms.list", new { }, cancellationToken);

    public Task<JsonElement> GetRoomAsync(string roomId, CancellationToken cancellationToken = default) =>
        RequestResultAsync("rooms.get", new { room_id = roomId }, cancellationToken);

    public Task<JsonElement> AddRoomAsync(string name, CancellationToken cancellationToken = default) =>
        RequestResultAsync("rooms.add", new { name }, cancellationToken);

    public Task<JsonElement> RenameRoomAsync(
        string roomId,
        string name,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("rooms.rename", new { room_id = roomId, name }, cancellationToken);

    public Task<JsonElement> RemoveRoomAsync(string roomId, CancellationToken cancellationToken = default) =>
        RequestResultAsync("rooms.remove", new { room_id = roomId }, cancellationToken);

    public Task<JsonElement> SendDeviceCommandAsync(
        string deviceId,
        string service,
        int? brightnessPct = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "devices.command",
            new { device_id = deviceId, service, brightness_pct = brightnessPct },
            cancellationToken);

    public Task<JsonElement> ApproveRequestAsync(
        string requestId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("requests.approve", new { request_id = requestId }, cancellationToken);

    public Task<JsonElement> DenyRequestAsync(
        string requestId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("requests.deny", new { request_id = requestId }, cancellationToken);

    public Task<JsonElement> GetPeopleAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("people.list", new { }, cancellationToken);

    public Task<JsonElement> AddPersonAsync(
        string name,
        string role = "member",
        string? entityId = null,
        string? roomId = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "people.add",
            new { name, role, entity_id = entityId, room_id = roomId },
            cancellationToken);

    public Task<JsonElement> UpdatePersonAsync(
        string personId,
        string? name = null,
        string? role = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "people.update",
            new { person_id = personId, name, role },
            cancellationToken);

    public Task<JsonElement> RemovePersonAsync(
        string personId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("people.remove", new { person_id = personId }, cancellationToken);

    public Task<JsonElement> GetContextsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("contexts.list", new { }, cancellationToken);

    public Task<JsonElement> AddContextAsync(
        string label,
        string entityId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("contexts.add", new { label, entity_id = entityId }, cancellationToken);

    public Task<JsonElement> UpdateContextAsync(
        string contextId,
        string? label = null,
        string? entityId = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "contexts.update",
            new { context_id = contextId, label, entity_id = entityId },
            cancellationToken);

    public Task<JsonElement> RemoveContextAsync(
        string contextId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("contexts.remove", new { context_id = contextId }, cancellationToken);

    public Task<JsonElement> GetAutomationsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("automations.list", new { }, cancellationToken);

    public Task<JsonElement> GetAutomationOptionsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("automations.options", new { }, cancellationToken);

    public Task<JsonElement> CreateAutomationAsync(
        string sourceText,
        string timeOfDay,
        IReadOnlyList<int> weekdays,
        string targetDeviceId,
        string capability,
        string service,
        string? interpretation = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "automations.create",
            new
            {
                source_text = sourceText,
                time_of_day = timeOfDay,
                weekdays,
                target_device_id = targetDeviceId,
                capability,
                service,
                interpretation,
            },
            cancellationToken);

    public Task<JsonElement> UpdateAutomationAsync(
        string ruleId,
        string sourceText,
        string timeOfDay,
        IReadOnlyList<int> weekdays,
        string? interpretation = null,
        string? justification = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "automations.update",
            new
            {
                rule_id = ruleId,
                source_text = sourceText,
                time_of_day = timeOfDay,
                weekdays,
                interpretation,
                justification,
            },
            cancellationToken);

    public Task<JsonElement> SetAutomationEnabledAsync(
        string ruleId,
        bool enabled,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "automations.enable",
            new { rule_id = ruleId, enabled },
            cancellationToken);

    public Task<JsonElement> ApproveAutomationAsync(
        string ruleId,
        string? justification = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "automations.approve",
            new { rule_id = ruleId, justification },
            cancellationToken);

    public Task<JsonElement> RevokeAutomationAsync(
        string ruleId,
        string justification,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "automations.revoke",
            new { rule_id = ruleId, justification },
            cancellationToken);

    public Task<JsonElement> GetModelsOverviewAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.overview", new { }, cancellationToken);

    public Task<JsonElement> GetModelsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.list", new { }, cancellationToken);

    public Task<JsonElement> GetModelJobsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.jobs", new { }, cancellationToken);

    public Task<JsonElement> CancelModelJobAsync(
        string jobId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.job.cancel", new { job_id = jobId }, cancellationToken);

    public Task<JsonElement> InspectModelUrlAsync(
        string url,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.inspect", new { url }, cancellationToken);

    public Task<JsonElement> DownloadModelAsync(
        string url,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.download", new { url }, cancellationToken);

    public Task<JsonElement> InstallModelFromUrlAsync(
        string url,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.install_url", new { url }, cancellationToken);

    public Task<JsonElement> InstallLocalModelAsync(
        string folder,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.install_local", new { folder }, cancellationToken);

    public Task<JsonElement> AddModelEndpointAsync(
        string url,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.add_endpoint", new { url }, cancellationToken);

    public Task<JsonElement> AddModelRootAsync(
        string path,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.add_root", new { path }, cancellationToken);

    public Task<JsonElement> ScanModelRootsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.scan", new { }, cancellationToken);

    public Task<JsonElement> RegisterModelAsync(
        string path,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.register", new { path }, cancellationToken);

    public Task<JsonElement> LoadModelAsync(
        string modelId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.load", new { id = modelId }, cancellationToken);

    public Task<JsonElement> UnloadModelAsync(
        string modelId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.unload", new { id = modelId }, cancellationToken);

    public Task<JsonElement> RemoveModelAsync(
        string modelId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.remove", new { id = modelId }, cancellationToken);

    public Task<JsonElement> AssignModelAsync(
        string role,
        string? modelId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("models.assign", new { role, id = modelId }, cancellationToken);

    public Task<JsonElement> GetSystemDiagnosticsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.diagnostics", new { }, cancellationToken);

    public Task<JsonElement> ProbeSystemProviderAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.probe", new { }, cancellationToken);

    public Task<JsonElement> GetBackupsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.backups", new { }, cancellationToken);

    public Task<JsonElement> CreateBackupAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.backup.create", new { }, cancellationToken);

    public Task<JsonElement> RestoreBackupAsync(
        string backupId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.backup.restore", new { id = backupId }, cancellationToken);

    public Task<JsonElement> DeleteBackupAsync(
        string backupId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.backup.delete", new { id = backupId }, cancellationToken);

    public Task<JsonElement> GetServiceStatusAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.service", new { }, cancellationToken);

    public Task<JsonElement> InstallServiceAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.service.install", new { }, cancellationToken);

    public Task<JsonElement> UninstallServiceAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("system.service.uninstall", new { }, cancellationToken);

    public Task<JsonElement> GetProjectsAsync(
        string? status = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("projects.list", new { status }, cancellationToken);

    public Task<JsonElement> GetProjectAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("projects.get", new { project_id = projectId }, cancellationToken);

    public Task<JsonElement> CreateProjectAsync(
        string title,
        string? description = null,
        string? status = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "projects.create",
            new { title, description, status },
            cancellationToken);

    public Task<JsonElement> UpdateProjectAsync(
        string projectId,
        int revision,
        string? title = null,
        string? description = null,
        string? status = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "projects.update",
            new { project_id = projectId, revision, title, description, status },
            cancellationToken);

    public Task<JsonElement> ArchiveProjectAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("projects.archive", new { project_id = projectId }, cancellationToken);

    public Task<JsonElement> AttachProjectResourceAsync(
        string projectId,
        string resourceId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "projects.attach",
            new { project_id = projectId, resource_id = resourceId },
            cancellationToken);

    public Task<JsonElement> DetachProjectResourceAsync(
        string projectId,
        string resourceId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "projects.detach",
            new { project_id = projectId, resource_id = resourceId },
            cancellationToken);

    public Task<JsonElement> GetProjectResourcesAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("projects.attached", new { project_id = projectId }, cancellationToken);

    public Task<JsonElement> GetTasksAsync(
        string? view = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("tasks.list", new { view }, cancellationToken);

    public Task<JsonElement> GetTaskAsync(
        string taskId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("tasks.get", new { task_id = taskId }, cancellationToken);

    public Task<JsonElement> CreateTaskAsync(
        string title,
        string? projectId = null,
        string? detail = null,
        string? priority = null,
        string? dueAt = null,
        string? recurrence = null,
        string? state = null,
        IReadOnlyList<string>? dependencyIds = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "tasks.create",
            new
            {
                title,
                project_id = projectId,
                detail,
                priority,
                due_at = dueAt,
                recurrence,
                state,
                dependency_ids = dependencyIds ?? Array.Empty<string>(),
            },
            cancellationToken);

    public Task<JsonElement> UpdateTaskAsync(
        string taskId,
        int revision,
        string? title = null,
        string? detail = null,
        string? state = null,
        string? priority = null,
        string? dueAt = null,
        string? projectId = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "tasks.update",
            new
            {
                task_id = taskId,
                revision,
                title,
                detail,
                state,
                priority,
                due_at = dueAt,
                project_id = projectId,
            },
            cancellationToken);

    public Task<JsonElement> CompleteTaskAsync(
        string taskId,
        int revision,
        IReadOnlyList<string>? evidenceRefs = null,
        string? evidenceSource = null,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "tasks.complete",
            new
            {
                task_id = taskId,
                revision,
                evidence_refs = evidenceRefs ?? Array.Empty<string>(),
                evidence_source = evidenceSource,
            },
            cancellationToken);

    public Task<JsonElement> AddTaskDependencyAsync(
        string taskId,
        string dependsOnTaskId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "tasks.add_dependency",
            new { task_id = taskId, depends_on_task_id = dependsOnTaskId },
            cancellationToken);

    public Task<JsonElement> RemoveTaskDependencyAsync(
        string taskId,
        string dependsOnTaskId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "tasks.remove_dependency",
            new { task_id = taskId, depends_on_task_id = dependsOnTaskId },
            cancellationToken);

    public Task<JsonElement> GetComputerFilesAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("computer.files.list", new { }, cancellationToken);

    public Task<JsonElement> GetComputerAppsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("computer.apps.list", new { }, cancellationToken);

    public Task<JsonElement> GetComputerWindowsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("computer.windows.list", new { }, cancellationToken);

    public Task<JsonElement> FocusWindowAsync(
        string resourceId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "computer.window.focus",
            new { resource_id = resourceId },
            cancellationToken);

    public Task<JsonElement> GetComputerActivityAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("computer.activity.list", new { }, cancellationToken);

    public Task<JsonElement> SetComputerObservationAsync(
        bool enabled,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("computer.observation.set", new { enabled }, cancellationToken);

    public Task<JsonElement> GetBrowserTabsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("browser.tabs.list", new { }, cancellationToken);

    public Task<JsonElement> FocusBrowserTabAsync(
        string resourceId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("browser.tab.focus", new { resource_id = resourceId }, cancellationToken);

    public Task<JsonElement> OpenBrowserTabAsync(
        string browser,
        string url,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("browser.tab.open", new { browser, url }, cancellationToken);

    public Task<JsonElement> CloseBrowserTabAsync(
        string resourceId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("browser.tab.close", new { resource_id = resourceId }, cancellationToken);

    public Task<JsonElement> ConfirmCloseBrowserTabAsync(
        string requestId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("browser.tab.close.confirm", new { request_id = requestId }, cancellationToken);

    public Task<JsonElement> GetCalendarEventsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("calendar.events.list", new { }, cancellationToken);

    public Task<JsonElement> AddCalendarSourceAsync(
        string path,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("calendar.sources.add", new { path }, cancellationToken);

    public Task<JsonElement> ProposeTaskForEventAsync(
        string eventId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("calendar.event.propose_task", new { event_id = eventId }, cancellationToken);

    public Task<JsonElement> GetEmailStatusAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("email.status", new { }, cancellationToken);

    public Task<JsonElement> GetEmailMessagesAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("email.messages.list", new { }, cancellationToken);

    public Task<JsonElement> SetEmailMaildirAsync(
        string path,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("email.maildir.set", new { path }, cancellationToken);

    public Task<JsonElement> GetTodayCardsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("today.cards", new { }, cancellationToken);

    public Task<JsonElement> DismissTodayCardAsync(
        string cardId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("today.dismiss", new { card_id = cardId }, cancellationToken);

    public Task<JsonElement> GetRelationshipsForAsync(
        string resourceId,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync("relationships.for", new { resource_id = resourceId }, cancellationToken);

    public Task<JsonElement> GetExtensionsAsync(CancellationToken cancellationToken = default) =>
        RequestResultAsync("extensions.list", new { }, cancellationToken);

    public Task<JsonElement> SetExportConsumerEnabledAsync(
        string pluginId,
        bool enabled,
        CancellationToken cancellationToken = default) =>
        RequestResultAsync(
            "extensions.export_consumers.set_enabled",
            new { plugin_id = pluginId, enabled },
            cancellationToken);

    private async Task<JsonElement> RequestResultAsync(
        string method,
        object parameters,
        CancellationToken cancellationToken)
    {
        using var response = await RequestDocumentAsync(method, parameters, cancellationToken);
        EnsureSuccess(response);
        return response.RootElement.GetProperty("result").Clone();
    }

    private async Task<JsonDocument> RequestDocumentAsync(
        string method,
        object parameters,
        CancellationToken cancellationToken)
    {
        // One request at a time on the single pipe: the frame protocol is a
        // strict request/response alternation and concurrent page loads
        // (rooms + automations + contexts) would interleave frames.
        await _requestLock.WaitAsync(cancellationToken);
        try
        {
            return await RequestDocumentUnlockedAsync(method, parameters, cancellationToken);
        }
        finally
        {
            _requestLock.Release();
        }
    }

    private async Task<JsonDocument> RequestDocumentUnlockedAsync(
        string method,
        object parameters,
        CancellationToken cancellationToken)
    {
        if (_pipe is null || !_pipe.IsConnected)
        {
            throw new InvalidOperationException("HAVEN Core is not connected.");
        }
        var requestId = Guid.NewGuid().ToString("N");
        var request = new
        {
            version = ProtocolVersion,
            kind = "request",
            request_id = requestId,
            method,
            @params = parameters,
        };
        var body = JsonSerializer.SerializeToUtf8Bytes(request, _json);
        if (body.Length > MaxFrameBytes)
        {
            throw new InvalidOperationException("HAVEN IPC request is too large.");
        }
        var frame = new byte[4 + body.Length];
        BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(0, 4), (uint)body.Length);
        body.CopyTo(frame.AsSpan(4));
        await _pipe.WriteAsync(frame, cancellationToken);
        await _pipe.FlushAsync(cancellationToken);

        var header = await ReadExactlyAsync(4, cancellationToken);
        var length = BinaryPrimitives.ReadUInt32LittleEndian(header);
        if (length == 0 || length > MaxFrameBytes)
        {
            throw new InvalidOperationException("HAVEN IPC response has an invalid frame length.");
        }
        var responseBody = await ReadExactlyAsync((int)length, cancellationToken);
        return JsonDocument.Parse(responseBody);
    }

    private async Task<byte[]> ReadExactlyAsync(int length, CancellationToken cancellationToken)
    {
        var buffer = new byte[length];
        var offset = 0;
        while (offset < length)
        {
            var count = await _pipe!.ReadAsync(buffer.AsMemory(offset, length - offset), cancellationToken);
            if (count == 0)
            {
                throw new EndOfStreamException("HAVEN Core closed the IPC pipe.");
            }
            offset += count;
        }
        return buffer;
    }

    private static void EnsureSuccess(JsonDocument response)
    {
        var root = response.RootElement;
        if (!root.TryGetProperty("ok", out var ok) || !ok.GetBoolean())
        {
            var error = root.TryGetProperty("error", out var errorValue)
                ? errorValue.GetString()
                : "HAVEN Core rejected the IPC request.";
            throw new InvalidOperationException(error);
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_pipe is not null)
        {
            await _pipe.DisposeAsync();
            _pipe = null;
        }
    }
}
