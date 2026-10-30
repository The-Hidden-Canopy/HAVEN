using System.Text.Json;
using Haven.SessionContract;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
    private FrameworkElement SessionControls()
    {
        var panel = new StackPanel { Spacing = 8 };
        panel.Children.Add(WorkflowText("Shared sessions"));
        var choices = new ComboBox { Header = "Authorized session", MinWidth = 240 };
        var input = new TextBox { Header = "Message", AcceptsReturn = true };
        var output = WorkflowText("An existing provider and explicit session grants are required.");
        var jobsAndApprovals = new StackPanel { Spacing = 6 };
        var api = new SessionClient(new LocalSessionTransport(_client!));
        long cursor = 0;
        int selectionGeneration = 0;
        SessionPending? uncertain = SessionPendingJournal.Read();
        string Selected() => (choices.SelectedItem as ComboBoxItem)?.Tag as string
            ?? throw new InvalidOperationException("Select an authorized session.");
        void Show(JsonElement value) => output.Text = JsonSerializer.Serialize(value, new JsonSerializerOptions { WriteIndented = true });
        async Task Mutate(Func<string,string,Task<JsonElement>> invoke, string? expectedSession=null)
        {
            if (uncertain != null) throw new InvalidOperationException("Inspect the previous command before another submission.");
            var session=Selected();if(expectedSession is not null && expectedSession!=session)throw new InvalidOperationException("Reload the selected session before acting.");var id=Guid.NewGuid().ToString();
            uncertain=new SessionPending(session,id);SessionPendingJournal.Write(uncertain);
            try { Show(await invoke(session,id));SessionPendingJournal.Clear();uncertain=null; }
            catch (SessionFailureException ex) when (ex.NotExecuted) { SessionPendingJournal.Clear();uncertain=null;output.Text="The host rejected the request before execution."; }
            catch { output.Text=$"Outcome uncertain. Inspect command {id} before retrying.";throw; }
        }
        choices.SelectionChanged += (_, _) => { selectionGeneration++;cursor = 0; jobsAndApprovals.Children.Clear(); output.Text = "Load this session to inspect current state."; };
        panel.Children.Add(choices); panel.Children.Add(input);
        panel.Children.Add(WorkflowButton("Load authorized sessions", async () =>
        {
            await api.CapabilitiesAsync(Guid.NewGuid().ToString());
            var result = await api.SessionsAsync(Guid.NewGuid().ToString());
            choices.Items.Clear();
            foreach (var item in result.GetProperty("sessions").EnumerateArray())
                choices.Items.Add(new ComboBoxItem { Content = item.GetProperty("session_id").GetString(), Tag = item.GetProperty("session_id").GetString() });
            output.Text = choices.Items.Count == 0 ? "No authorized sessions." : "Select a session.";
        }));
        panel.Children.Add(WorkflowButton("Inspect jobs and approvals", async () =>
        {
            var session=Selected();var generation=selectionGeneration;
            var result=await api.SnapshotAsync(session,Guid.NewGuid().ToString());if(generation!=selectionGeneration)return;Show(result);jobsAndApprovals.Children.Clear();
            foreach(var job in result.GetProperty("jobs").EnumerateArray())
            {
                var jobId=job.GetProperty("job_id").GetString()!;
                jobsAndApprovals.Children.Add(WorkflowButton("Request cancellation: "+jobId,()=>Mutate((s,r)=>api.CancelAsync(s,jobId,r),session)));
            }
            foreach(var approval in result.GetProperty("approvals").EnumerateArray())
            {
                if(approval.GetProperty("status").GetString()!="pending")continue;
                var approvalId=approval.GetProperty("approval_id").GetString()!;var revision=approval.GetProperty("revision").GetInt64();
                jobsAndApprovals.Children.Add(WorkflowText(approval.GetProperty("description").GetString()??approvalId));
                jobsAndApprovals.Children.Add(WorkflowButton("Approve",()=>Mutate((s,r)=>api.DecideAsync(s,approvalId,revision,true,r),session)));
                jobsAndApprovals.Children.Add(WorkflowButton("Deny",()=>Mutate((s,r)=>api.DecideAsync(s,approvalId,revision,false,r),session)));
            }
        }));
        panel.Children.Add(jobsAndApprovals);
        panel.Children.Add(WorkflowButton("Replay messages", async () =>
        {
            var generation=selectionGeneration;var result = await api.ReplayAsync(Selected(),cursor,Guid.NewGuid().ToString());if(generation!=selectionGeneration)return;
            cursor = result.GetProperty("next_cursor").GetInt64(); Show(result);
        }));
        panel.Children.Add(WorkflowButton("Submit message",()=>Mutate((s,r)=>api.SubmitAsync(s,input.Text,r))));
        panel.Children.Add(WorkflowButton("Inspect last uncertain command", async () =>
        {
            if (uncertain is null) return;
            await api.CapabilitiesAsync(Guid.NewGuid().ToString());
            var result = await api.CommandStatusAsync(uncertain.SessionId,uncertain.CommandId,Guid.NewGuid().ToString()); Show(result);
            if (result.GetProperty("state").GetString() == "recorded") { SessionPendingJournal.Clear(); uncertain = null; }
        }));
        panel.Children.Add(output);
        return panel;
    }
}
