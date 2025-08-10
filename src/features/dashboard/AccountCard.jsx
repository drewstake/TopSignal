import { Coins } from "lucide-react";
import Card from "../../components/ui/Card";
import Label from "../../components/ui/Label";
import Select from "../../components/ui/Select";
import { fmtUSD } from "../../lib/format";

export default function AccountCard({ accounts, selectedAccount, onChange, onSwitch }) {
  return (
    <Card className="p-5">
      <div className="flex items-center gap-3 mb-4">
        <Coins className="h-5 w-5 text-indigo-400" />
        <h2 className="text-sm font-semibold tracking-wide">Account</h2>
      </div>
      <Label>Choose Account</Label>
      <Select
        value={selectedAccount}
        onChange={(e) => {
          onChange(e.target.value);
          const acc = accounts.find((a) => String(a.id) === e.target.value);
          if (acc && onSwitch) onSwitch(acc);
        }}
      >
        {accounts.length === 0 ? (
          <option value="">No active accounts</option>
        ) : (
          accounts.map((a) => (
            <option key={a.id} value={String(a.id)}>
              {a.name} • {fmtUSD(a.balance)}
            </option>
          ))
        )}
      </Select>
    </Card>
  );
}
