using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Mirrorly.Desktop.Services;

public sealed record ProtocolMessage(
    [property: JsonPropertyName("protocol_version")] int ProtocolVersion,
    [property: JsonPropertyName("message_type")] string MessageType,
    [property: JsonPropertyName("request_id")] string? RequestId,
    [property: JsonPropertyName("payload")] JsonElement Payload);

public static class Protocol
{
    public const int Version = 1;
    public const int MaxFrameBytes = 65536; // Includes the LF delimiter.
    private static readonly UTF8Encoding Utf8 = new(false, true);

    public static ProtocolMessage Parse(string json)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object ||
            !root.TryGetProperty("protocol_version", out var version) ||
            version.ValueKind != JsonValueKind.Number ||
            !version.TryGetInt32(out var number) || number != Version ||
            !root.TryGetProperty("message_type", out var kind) || kind.ValueKind != JsonValueKind.String ||
            !root.TryGetProperty("request_id", out var id) ||
            !root.TryGetProperty("payload", out var payload) || payload.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("Invalid Phase 1A protocol envelope or version.");
        var type = kind.GetString()!;
        if (type is not ("request" or "response" or "event" or "error"))
            throw new InvalidDataException("Unknown message_type.");
        string? requestId = id.ValueKind == JsonValueKind.String ? id.GetString() : null;
        if (id.ValueKind is not (JsonValueKind.String or JsonValueKind.Null) ||
            (requestId is null && type != "error") ||
            (requestId is not null && requestId.Length is < 1 or > 128))
            throw new InvalidDataException("Invalid request_id.");
        return new(number, type, requestId, payload.Clone());
    }

    public static string Request(string id, string command) => JsonSerializer.Serialize(
        new ProtocolMessage(Version, "request", id, JsonSerializer.SerializeToElement(new { command })));

    public static async IAsyncEnumerable<string> ReadFramesAsync(Stream stream,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        var buffer = new byte[4096];
        using var frame = new MemoryStream();
        int count;
        while ((count = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) != 0)
        {
            for (var i = 0; i < count; i++)
            {
                if (buffer[i] == (byte)'\n')
                {
                    yield return Utf8.GetString(frame.GetBuffer(), 0, (int)frame.Length);
                    frame.SetLength(0);
                }
                else
                {
                    if (frame.Length >= MaxFrameBytes - 1)
                        throw new InvalidDataException("IPC frame exceeds 64 KiB.");
                    frame.WriteByte(buffer[i]);
                }
            }
        }
        if (frame.Length != 0)
            throw new InvalidDataException("Worker disconnected in an incomplete IPC frame.");
    }
}
