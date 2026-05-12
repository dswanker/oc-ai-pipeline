// src/components/Toast.jsx
export function Toast({ msg, type }) {
  return (
    <div className={`toast toast-${type}`}>{msg}</div>
  );
}
export default Toast;
