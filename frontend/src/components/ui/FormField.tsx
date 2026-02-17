import { Input, type InputProps } from "@/components/ui/Input";
import { cn } from "@/lib/cn";

type FormFieldProps = Omit<InputProps, "id"> & {
  id: string;
  label: string;
  hint?: string;
  error?: string;
};

export function FormField({ id, label, hint, error, className, ...props }: FormFieldProps) {
  const messageId = `${id}-message`;

  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block text-sm font-medium text-foreground">
        {label}
      </label>
      <Input
        id={id}
        className={cn(error && "border-danger focus:border-danger focus:ring-danger/35", className)}
        aria-invalid={Boolean(error)}
        aria-describedby={hint || error ? messageId : undefined}
        {...props}
      />
      {(hint || error) && (
        <p id={messageId} className={cn("text-xs", error ? "text-danger" : "text-muted")}>
          {error ?? hint}
        </p>
      )}
    </div>
  );
}
