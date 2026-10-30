import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import _ from "@/lib/translate"
import { useFrappePostCall, useSWRConfig } from "frappe-react-sdk"
import { useAtomValue } from "jotai"
import { Sparkles, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { bankRecDateAtom, selectedBankAccountAtom } from "./bankRecAtoms"

const AutoReconcileButton = () => {
    const selectedBank = useAtomValue(selectedBankAccountAtom)
    const dates = useAtomValue(bankRecDateAtom)
    const { mutate } = useSWRConfig()

    const { call: triggerAutoReconcile, loading } = useFrappePostCall<{
        message: {
            queued?: boolean,
            reconciled?: number,
            partially_reconciled?: number,
            message?: string,
            indicator?: string
        }
    }>('mint.apis.bank_reconciliation.auto_reconcile_vouchers')

    const onAutoReconcile = async () => {
        if (!selectedBank?.name) {
            toast.error(_("Please select a bank account first."))
            return
        }

        try {
            const res = await triggerAutoReconcile({
                bank_account: selectedBank.name,
                from_date: dates.fromDate,
                to_date: dates.toDate,
            })

            const data = res?.message
            if (data?.queued) {
                toast.info(data.message || _("Auto-reconciliation started in the background."))
            } else if (data?.reconciled && data.reconciled > 0) {
                toast.success(data.message || _("{0} transactions auto-reconciled.", [String(data.reconciled)]))
                // Refresh all queries
                mutate(() => true, undefined, { revalidate: true })
            } else {
                toast.info(data?.message || _("No exact matches found for auto-reconciliation."))
            }
        } catch (err: any) {
            toast.error(err?.message || _("Failed to execute auto-reconciliation."))
        }
    }

    if (!selectedBank) {
        return null
    }

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <Button
                    variant="outline"
                    size="sm"
                    className="h-9 gap-1.5 text-xs font-medium text-emerald-600 hover:text-emerald-700 hover:bg-emerald-50 border-emerald-200"
                    onClick={onAutoReconcile}
                    disabled={loading}
                >
                    {loading ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                        <Sparkles className="h-3.5 w-3.5" />
                    )}
                    <span>{_("Auto Reconcile")}</span>
                </Button>
            </TooltipTrigger>
            <TooltipContent>
                {_("Automatically match and reconcile exact 1:1 transactions with existing ERPNext vouchers")}
            </TooltipContent>
        </Tooltip>
    )
}

export default AutoReconcileButton
