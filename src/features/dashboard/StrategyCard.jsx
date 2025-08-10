import { Bot, Play, Square } from "lucide-react";
import Card from "../../components/ui/Card";
import Label from "../../components/ui/Label";
import Select from "../../components/ui/Select";
import Button from "../../components/ui/Button";

export default function StrategyCard({
  strategies,
  selectedStrategy,
  onChange,
  onStart,
  onStop,
  botRunning,
  disabled,
}) {
  return (
    <Card className="p-5">
      <div className="flex items-center gap-3 mb-4">
        <Bot className="h-5 w-5 text-fuchsia-400" />
        <h2 className="text-sm font-semibold tracking-wide">Strategy</h2>
      </div>
      <Label>Bot Strategy</Label>
      <Select value={selectedStrategy} onChange={(e) => onChange(e.target.value)}>
        {strategies.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </Select>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <Button onClick={onStart} disabled={disabled || botRunning || !selectedStrategy}>
          <Play className="h-4 w-4" /> Start
        </Button>
        <Button variant="danger" onClick={onStop} disabled={disabled || !botRunning}>
          <Square className="h-4 w-4" /> Stop
        </Button>
      </div>
      <p className="mt-3 text-xs text-zinc-400">
        Status: {botRunning ? <span className="text-emerald-400">Running</span> : <span className="text-zinc-300">Idle</span>}
      </p>
    </Card>
  );
}
