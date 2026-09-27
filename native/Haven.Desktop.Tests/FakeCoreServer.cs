using System.Buffers.Binary;
using System.IO.Pipes;
using System.Text;

namespace Haven.Desktop.Tests;

/// <summary>A minimal stand-in for HAVEN Core's events pipe server: accepts
/// one connection, performs the same length-prefixed `host.authenticate`
/// handshake `HavenEventClient` expects, and hands the raw stream back so a
/// test can keep it open, push frames, or close it to simulate a dropped
/// connection.</summary>
internal static class FakeCoreServer
{
    public static async Task<NamedPipeServerStream> AcceptAndAuthenticateAsync(
        string eventsPipeName, CancellationToken cancellationToken = default)
    {
        // Unlimited instances: a test may keep an old (silently stalled)
        // server instance open while accepting a second connection on the
        // same pipe name, the same way a real reconnect can race the old
        // transport's teardown.
        var server = new NamedPipeServerStream(
            eventsPipeName, PipeDirection.InOut, NamedPipeServerStream.MaxAllowedServerInstances,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
        await server.WaitForConnectionAsync(cancellationToken);
        await ReadFrameAsync(server, cancellationToken); // the host.authenticate request; contents unchecked
        await WriteFrameAsync(server, "{\"ok\":true}", cancellationToken);
        return server;
    }

    public static async Task WriteFrameAsync(Stream stream, string json, CancellationToken cancellationToken = default)
    {
        var body = Encoding.UTF8.GetBytes(json);
        var frame = new byte[4 + body.Length];
        BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(0, 4), (uint)body.Length);
        body.CopyTo(frame.AsSpan(4));
        await stream.WriteAsync(frame, cancellationToken);
        await stream.FlushAsync(cancellationToken);
    }

    public static async Task<byte[]> ReadFrameAsync(Stream stream, CancellationToken cancellationToken = default)
    {
        var header = await ReadExactlyAsync(stream, 4, cancellationToken);
        var length = BinaryPrimitives.ReadUInt32LittleEndian(header);
        return await ReadExactlyAsync(stream, (int)length, cancellationToken);
    }

    private static async Task<byte[]> ReadExactlyAsync(Stream stream, int length, CancellationToken cancellationToken)
    {
        var buffer = new byte[length];
        var offset = 0;
        while (offset < length)
        {
            var count = await stream.ReadAsync(buffer.AsMemory(offset, length - offset), cancellationToken);
            if (count == 0)
            {
                throw new EndOfStreamException();
            }
            offset += count;
        }
        return buffer;
    }
}
