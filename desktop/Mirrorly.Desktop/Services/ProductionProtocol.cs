using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Mirrorly.Desktop.Services;

public sealed record ProductionVersion(int major, int minor);
public sealed record ProductionMessage(
    [property: JsonPropertyName("protocol")] string Protocol,
    [property: JsonPropertyName("protocol_version")] ProductionVersion? Version,
    [property: JsonPropertyName("message_type")] string MessageType,
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("request_id")] string? RequestId,
    [property: JsonPropertyName("operation_id")] string? OperationId,
    [property: JsonPropertyName("interaction_id")] string? InteractionId,
    [property: JsonPropertyName("payload")] JsonElement Payload);

public static class ProductionProtocol
{
    public const string Identity = "mirrorly.worker";
    public static readonly ProductionVersion Version = new(1, 0);
    public const int HandshakeBytes = 65536, FrameBytes = 1048576;
    public const int MaxDepth = 32, MaxCollection = 4096, MaxNodes = 16384, MaxString = 32768;
    private static readonly UTF8Encoding Utf8 = new(false, true);
    private static readonly HashSet<string> Fields = ["protocol", "protocol_version", "message_type", "session_id", "request_id", "operation_id", "interaction_id", "payload"];

    public static ProductionMessage Parse(string text)
    {
        using var document = JsonDocument.Parse(text, new JsonDocumentOptions { MaxDepth = MaxDepth });
        var nodes = 0;
        ValidateTree(document.RootElement, 1, ref nodes);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !Fields.SetEquals(root.EnumerateObject().Select(p => p.Name)))
            throw new InvalidDataException("Invalid production envelope fields.");
        if (root.GetProperty("protocol").GetString() != Identity) throw new InvalidDataException("Wrong protocol identity.");
        var kind = root.GetProperty("message_type").GetString();
        if (kind is not ("hello" or "initialize" or "request" or "response" or "event" or "protocol_error" or "interaction_request" or "interaction_response"))
            throw new InvalidDataException("Unknown message type.");
        var version = root.GetProperty("protocol_version");
        if (version.ValueKind != JsonValueKind.Null &&
            (version.ValueKind != JsonValueKind.Object || version.EnumerateObject().Count() != 2 ||
             !version.TryGetProperty("major", out var major) || !version.TryGetProperty("minor", out var minor) ||
             !major.TryGetInt32(out var ma) || !minor.TryGetInt32(out var mi) || ma < 0 || mi < 0))
            throw new InvalidDataException("Invalid version.");
        foreach (var name in new[] { "session_id", "request_id", "operation_id", "interaction_id" })
        {
            var item = root.GetProperty(name);
            if (item.ValueKind != JsonValueKind.Null && (item.ValueKind != JsonValueKind.String || item.GetString()!.Length is < 1 or > 128))
                throw new InvalidDataException("Invalid correlation identifier.");
        }
        if (root.GetProperty("payload").ValueKind != JsonValueKind.Object) throw new InvalidDataException("Invalid payload.");
        return JsonSerializer.Deserialize<ProductionMessage>(text)!;
    }

    private static void ValidateTree(JsonElement item, int depth, ref int nodes)
    {
        if (++nodes > MaxNodes || depth > MaxDepth) throw new InvalidDataException("JSON node/depth limit.");
        if (item.ValueKind == JsonValueKind.Object)
        {
            var keys = new HashSet<string>();
            foreach (var property in item.EnumerateObject())
            {
                if (!keys.Add(property.Name) || keys.Count > MaxCollection) throw new InvalidDataException("Duplicate key/collection limit.");
                if (++nodes > MaxNodes || depth + 1 > MaxDepth) throw new InvalidDataException("JSON node/depth limit.");
                CheckString(property.Name);
                ValidateTree(property.Value, depth + 1, ref nodes);
            }
        }
        else if (item.ValueKind == JsonValueKind.Array)
        {
            if (item.GetArrayLength() > MaxCollection) throw new InvalidDataException("Collection limit.");
            foreach (var child in item.EnumerateArray()) ValidateTree(child, depth + 1, ref nodes);
        }
        else if (item.ValueKind == JsonValueKind.String) CheckString(item.GetString()!);
        else if (item.ValueKind == JsonValueKind.Number && (!item.TryGetDouble(out var number) || !double.IsFinite(number)))
            throw new InvalidDataException("Non-finite JSON number.");
    }

    private static void CheckString(string text)
    {
        _ = Utf8.GetByteCount(text);
        if (text.EnumerateRunes().Count() > MaxString) throw new InvalidDataException("String limit.");
    }

    public static byte[] Encode(ProductionMessage message, int limit = FrameBytes)
    {
        var text = JsonSerializer.Serialize(message);
        _ = Parse(text);
        var bytes = Utf8.GetBytes(text + "\n");
        if (bytes.Length > limit) throw new InvalidDataException("Outbound frame limit.");
        return bytes;
    }

    public static async IAsyncEnumerable<ProductionMessage> ReadFramesAsync(Stream stream, Func<int> limit,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        var buffer = new byte[4096];
        using var frame = new MemoryStream();
        int count;
        while ((count = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) != 0)
        {
            for (var index = 0; index < count; index++)
            {
                var value = buffer[index];
                if (value == 10)
                {
                    if (frame.Length == 0) throw new InvalidDataException("Empty frame.");
                    yield return Parse(Utf8.GetString(frame.GetBuffer(), 0, (int)frame.Length));
                    frame.SetLength(0);
                }
                else
                {
                    if (frame.Length >= limit() - 1) throw new InvalidDataException("Inbound frame limit.");
                    frame.WriteByte(value);
                }
            }
        }
        if (frame.Length != 0) throw new InvalidDataException("Truncated frame.");
    }
}
