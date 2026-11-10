'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation } from '@tanstack/react-query';
import * as api from '@/lib/api';
import { useCommodityGroups } from '@/lib/hooks/useCommodityGroups';
import { Button, Input, Alert, Loader } from '@/components/base';

interface OrderLine {
  positionDescription: string;
  unitPrice: number;
  amount: number;
  unit: string;
  totalPrice: number;
  catalogArticleId?: string;
}

export default function NewRequestPage() {
  const router = useRouter();
  const uploadVersion = useRef(0);
  const submittingNow = useRef(false);
  const { data: commodityGroups } = useCommodityGroups();

  // Form state
  const [requestorName, setRequestorName] = useState('');
  const [title, setTitle] = useState('');
  const [vendorName, setVendorName] = useState('');
  const [vatId, setVatId] = useState('');
  const [department, setDepartment] = useState('');
  const [commodityGroupId, setCommodityGroupId] = useState<number | null>(null);
  const [orderLines, setOrderLines] = useState<OrderLine[]>([]);
  const [totalCost, setTotalCost] = useState<number>(0);
  const [keepQuotedTotal, setKeepQuotedTotal] = useState(false);
  const [pdfReviewed, setPdfReviewed] = useState(false);

  // UI state
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [warning, setWarning] = useState('');
  const [extracting, setExtracting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [suggestions, setSuggestions] = useState<Record<number, api.ArticleSuggestion[]>>({});
  const [findingSimilar, setFindingSimilar] = useState<number | null>(null);
  const [similarMessage, setSimilarMessage] = useState<Record<number, string>>({});
  const [unreviewedValues, setUnreviewedValues] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    const timer = setTimeout(async () => {
      const results = await Promise.all(orderLines.map(async (line) => {
        if (line.positionDescription.trim().length < 3 || !line.unit.trim() || line.amount <= 0) return [];
        try { return await api.articles.suggestions(line.positionDescription, line.unit, vendorName, line.amount); }
        catch { return []; }
      }));
      if (active) setSuggestions(Object.fromEntries(results.map((items, index) => [index, items])));
    }, 350);
    return () => { active = false; clearTimeout(timer); };
  }, [orderLines, vendorName]);

  const findSimilar = async (index: number) => {
    const line = orderLines[index];
    setFindingSimilar(index);
    setSimilarMessage((current) => ({ ...current, [index]: '' }));
    try {
      const found = await api.articles.semanticSuggestions(line.positionDescription, line.unit, vendorName, line.unitPrice, line.amount);
      setSuggestions((current) => {
        const existing = current[index] || [];
        const ids = new Set(existing.map((item) => item.id));
        return { ...current, [index]: [...existing, ...found.filter((item) => !ids.has(item.id))] };
      });
      if (!found.length) setSimilarMessage((current) => ({ ...current, [index]: 'No comparable catalog article found.' }));
    } catch (err) {
      setSimilarMessage((current) => ({ ...current, [index]: err instanceof Error ? err.message : 'Search unavailable.' }));
    } finally {
      setFindingSimilar(null);
    }
  };

  // PDF parsing mutation
  const parsePDFMutation = useMutation({
    mutationFn: async (file: File) => {
      const result = await api.pdf.parse(file);
      if (!result.text) {
        throw new Error('Failed to parse PDF');
      }
      return result.text;
    },
  });

  // Handle file upload and extraction
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const uploadedFile = e.target.files?.[0];
    if (!uploadedFile) return;

    if (uploadedFile.type !== 'application/pdf') {
      setError('Please upload a PDF file');
      return;
    }

    const version = ++uploadVersion.current;

    setFile(uploadedFile);
    setCommodityGroupId(null);
    setSuggestions({});
    setOrderLines([]);
    setTitle(''); setVendorName(''); setVatId(''); setDepartment(''); setTotalCost(0);
    setUnreviewedValues([]);
    setPdfReviewed(false);
    setError('');
    setSuccess('');
    setWarning('');
    setExtracting(true);

    try {
      // Step 1: Parse PDF
      const pdfText = await parsePDFMutation.mutateAsync(uploadedFile);

      // Step 2: Extract structured data AND classify commodity group in one AI call
      const extractionResult = await api.extraction.extract(pdfText);
      if (version !== uploadVersion.current) return;
      
      if (!extractionResult.success || !extractionResult.data) {
        throw new Error(extractionResult.error || 'Failed to extract data');
      }

      const extracted = extractionResult.data;

      // Step 3: Auto-fill form (including commodity group from extraction)
      setTitle(extracted.title || '');
      setVendorName(extracted.vendorName || '');
      setVatId(extracted.vatId || '');
      setDepartment(extracted.department || '');
      setOrderLines((extracted.orderLines || []).map((line) => ({
        ...line, positionDescription: line.positionDescription ?? '', unit: line.unit ?? '',
        unitPrice: line.unitPrice ?? 0, amount: line.amount ?? 0,
        totalPrice: line.totalPrice ?? 0,
      })));
      setTotalCost(extracted.totalCost || 0);
      setKeepQuotedTotal(extracted.totalCost !== null);
      setUnreviewedValues([
        ...extracted.orderLines.flatMap((line, index) =>
          (['unitPrice', 'amount', 'totalPrice'] as const)
            .filter((field) => line[field] === null)
            .map((field) => `${index}:${field}`)),
        ...(extracted.totalCost === null ? ['totalCost'] : []),
      ]);
      
      // Set commodity group (now included in extraction)
      setCommodityGroupId(extracted.commodityGroupId);

      // Show warnings for missing fields
      const issues = [
        ...(extractionResult.missingFields?.length ? [`Missing: ${extractionResult.missingFields.join(', ')}`] : []),
        ...(extractionResult.warnings || []),
      ];
      if (issues.length) setWarning(`${issues.join(' ')} Please review the quote before submitting.`);

      setSuccess('✓ PDF extracted successfully! Review and submit.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      if (version !== uploadVersion.current) return;
      console.error('Extraction error:', err);
      setError(err instanceof Error ? err.message : 'Failed to extract data from PDF');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } finally {
      if (version === uploadVersion.current) setExtracting(false);
    }
  };

  // Add order line manually
  const addOrderLine = () => {
    setPdfReviewed(false);
    setOrderLines([
      ...orderLines,
      {
        positionDescription: '',
        unitPrice: 0,
        amount: 1,
        unit: '',
        totalPrice: 0,
      },
    ]);
  };

  // Update order line
  const updateOrderLine = (index: number, field: keyof OrderLine, value: string | number) => {
    const updated = [...orderLines];
    updated[index] = { ...updated[index], [field]: value };
    if (field === 'positionDescription' || field === 'unitPrice' || field === 'unit' || field === 'amount') {
      delete updated[index].catalogArticleId;
      setPdfReviewed(false);
    }
    
    // Auto-calculate total price
    if (field === 'unitPrice' || field === 'amount') {
      updated[index].totalPrice = Math.round(updated[index].unitPrice * updated[index].amount * 100) / 100;
    }
    
    setOrderLines(updated);
    if (field === 'unitPrice' || field === 'amount') {
      setUnreviewedValues((current) => {
        const remaining = current.filter((key) => key !== `${index}:${field}`);
        return remaining.some((key) => key === `${index}:unitPrice` || key === `${index}:amount`)
          ? remaining : remaining.filter((key) => key !== `${index}:totalPrice`);
      });
    }
    
    // Update total cost
    if (!keepQuotedTotal) setTotalCost((current) => Math.round((current + updated[index].totalPrice - orderLines[index].totalPrice) * 100) / 100);
  };

  const applyNegotiatedPrice = (index: number, item: api.ArticleSuggestion) => {
    updateOrderLine(index, 'unitPrice', Number(item.unitPrice));
    setOrderLines((current) => current.map((line, i) => i === index ? { ...line, unitPrice: Number(item.unitPrice), totalPrice: Math.round(Number(item.unitPrice) * line.amount * 100) / 100, catalogArticleId: item.id } : line));
    setUnreviewedValues((current) => [...new Set([...current, 'totalCost'])]);
    setPdfReviewed(false);
    setWarning('Negotiated price applied. Recalculate the request total including tax and shipping, then review the PDF again before submitting.');
  };

  // Remove order line
  const removeOrderLine = (index: number) => {
    setPdfReviewed(false);
    const updated = orderLines.filter((_, i) => i !== index);
    setOrderLines(updated);
    setUnreviewedValues((current) => current.flatMap((key) => {
      if (key === 'totalCost') return [key];
      const [lineIndex, field] = key.split(':');
      const oldIndex = Number(lineIndex);
      return oldIndex === index ? [] : [`${oldIndex > index ? oldIndex - 1 : oldIndex}:${field}`];
    }));
    if (!keepQuotedTotal) setTotalCost((current) => Math.round((current - orderLines[index].totalPrice) * 100) / 100);
  };

  // Submit form
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submittingNow.current || extracting) return;
    setError('');
    setSuccess('');

    if (file && !pdfReviewed) {
      setError('Compare the extracted fields with the PDF and confirm your review before submitting.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    if (unreviewedValues.length) {
      setError('Some prices or quantities were missing from the PDF. Review and edit the flagged values before submitting.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    // Validation with scroll to top on error
    if (!commodityGroupId) {
      setError('Please select a commodity group');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    if (orderLines.length === 0) {
      setError('Please add at least one order line');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    let createdSuccessfully = false;
    try {
      submittingNow.current = true;
      setSubmitting(true);
      const created = await api.requests.create({
        requestorName,
        titleShortDescription: title,
        vendorName,
        vatId,
        commodityGroupId: commodityGroupId,
        totalCost,
        department,
        orderLines,
      });
      createdSuccessfully = true;

      // Attach the original PDF (if one was uploaded) so it shows on the detail page.
      if (file) {
        try {
          await api.requests.uploadDocument(created.id, file);
        } catch {
          router.push(`/dashboard/requests/${created.id}?attachment=failed`);
          return;
        }
      }

      setSuccess('Request created successfully! Redirecting...');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      setTimeout(() => {
        router.push('/dashboard/requests');
        router.refresh();
      }, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create request');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } finally {
      if (!createdSuccessfully) {
        submittingNow.current = false;
        setSubmitting(false);
      }
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">New Procurement Request</h1>
        <p className="text-gray-600 mt-2">
          Upload a vendor offer PDF or fill in the details manually
        </p>
      </div>

      {error && <Alert variant="error" className="mb-6">{error}</Alert>}
      {warning && <Alert variant="warning" className="mb-6">{warning}</Alert>}
      {success && <Alert variant="success" className="mb-6">{success}</Alert>}

      <form onSubmit={handleSubmit} className="space-y-8">
        {/* PDF Upload Section */}
        <div className="lio-card p-6">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Upload Vendor Offer (Optional)</h2>
          <p className="text-sm text-gray-600 mb-4">
            Upload a PDF to automatically extract vendor details and order items
          </p>

          <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-accent-deep/50 transition-colors">
            <input
              type="file"
              accept="application/pdf"
              onChange={handleFileChange}
              className="hidden"
              id="pdf-upload"
              disabled={extracting}
            />
            <label htmlFor="pdf-upload" className="cursor-pointer">
              {extracting ? (
                <div className="flex flex-col items-center">
                  <Loader size="lg" />
                  <p className="mt-4 text-sm text-gray-600">Extracting data from PDF...</p>
                </div>
              ) : file ? (
                <div>
                  <svg
                    className="mx-auto h-12 w-12 text-green-500"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M5 13l4 4L19 7"
                    />
                  </svg>
                  <p className="mt-2 text-sm font-medium text-gray-900">{file.name}</p>
                  <p className="mt-1 text-xs text-gray-500">Click to upload a different file</p>
                </div>
              ) : (
                <div>
                  <svg
                    className="mx-auto h-12 w-12 text-gray-400"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                    />
                  </svg>
                  <p className="mt-2 text-sm text-gray-600">
                    Click to upload or drag and drop
                  </p>
                  <p className="mt-1 text-xs text-gray-500">PDF up to 10MB</p>
                </div>
              )}
            </label>
          </div>
        </div>

        {/* Request Details */}
        <div className="lio-card p-6 space-y-4">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Request Details</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Requestor Name *"
              value={requestorName}
              onChange={(e) => setRequestorName(e.target.value)}
              required
              placeholder="John Doe"
            />

            <Input
              label="Department"
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              placeholder="IT Department"
            />
          </div>

          <Input
            label="Title / Short Description *"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            placeholder="Adobe Creative Cloud Subscription"
          />
        </div>

        {/* Vendor Information */}
        <div className="lio-card p-6 space-y-4">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Vendor Information</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Vendor Name *"
              value={vendorName}
              onChange={(e) => { setVendorName(e.target.value); setPdfReviewed(false); }}
              required
              placeholder="Adobe Systems"
            />

            <Input
              label="Tax ID"
              value={vatId}
              onChange={(e) => { setVatId(e.target.value); setPdfReviewed(false); }}
              placeholder="12-3456789"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Commodity Group *
            </label>
            <select
              value={commodityGroupId || ''}
              onChange={(e) => { setCommodityGroupId(Number(e.target.value)); setPdfReviewed(false); }}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-gray-900 focus:ring-2 focus:ring-accent-deep/60 focus:border-accent-deep/40"
            >
              <option value="">
                {commodityGroups ? 'Select a commodity group' : 'Loading...'}
              </option>
              {commodityGroups?.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.id} - {group.category} - {group.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Order Lines */}
        <div className="lio-card p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold tracking-tight text-ink">Order Lines *</h2>
            <Button type="button" onClick={addOrderLine} variant="outline" size="sm">
              + Add Line
            </Button>
          </div>

          {orderLines.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              No order lines yet. Add one to get started.
            </div>
          ) : (
            <div className="space-y-4">
              {orderLines.map((line, index) => (
                <div key={index} className="border border-gray-200 rounded-lg p-4">
                  <div className="flex items-start justify-between mb-4">
                    <h3 className="text-sm font-medium text-gray-700">Line {index + 1}</h3>
                    <button
                      type="button"
                      onClick={() => removeOrderLine(index)}
                      className="text-red-600 hover:text-red-700 text-sm"
                    >
                      Remove
                    </button>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
                    <div className="lg:col-span-2">
                      <Input
                        label="Description"
                        value={line.positionDescription}
                        onChange={(e) =>
                          updateOrderLine(index, 'positionDescription', e.target.value)
                        }
                        required
                        placeholder="Item description"
                      />
                    </div>

                    <Input
                      label="Unit Price ($)"
                      type="number"
                      step="0.01"
                      value={line.unitPrice}
                      onChange={(e) =>
                        updateOrderLine(index, 'unitPrice', parseFloat(e.target.value) || 0)
                      }
                      required
                    />

                    <Input
                      label="Amount"
                      type="number"
                      step="0.01"
                      value={line.amount}
                      onChange={(e) =>
                        updateOrderLine(index, 'amount', parseFloat(e.target.value) || 1)
                      }
                      required
                    />

                    <Input
                      label="Unit"
                      value={line.unit}
                      onChange={(e) => updateOrderLine(index, 'unit', e.target.value)}
                      required
                      placeholder="pcs, licenses, etc."
                    />
                  </div>

                  <div className="mt-2 text-right">
                    <span className="text-sm text-gray-600">Total: </span>
                    <span className="text-lg font-semibold tracking-tight text-ink">
                      ${line.totalPrice.toFixed(2)}
                    </span>
                  </div>
                  <button type="button" onClick={() => findSimilar(index)}
                    disabled={findingSimilar !== null || line.positionDescription.trim().length < 3 || !line.unit.trim() || line.amount <= 0}
                    className="mt-3 text-sm font-medium text-accent-deep disabled:opacity-50">
                    {findingSimilar === index ? 'Searching catalog…' : 'Find comparable catalog items'}
                  </button>
                  {similarMessage[index] && <p className="mt-1 text-sm text-ink/60">{similarMessage[index]}</p>}
                  {suggestions[index]?.length > 0 && (
                    <div className="mt-3 rounded border border-blue-200 bg-blue-50 p-3 text-sm">
                      <p className="font-medium">Negotiated catalog options — verify specifications, supplier and pack size before changing the request</p>
                      {suggestions[index].map((item) => (
                        <div key={item.id} className="mt-2">
                          <p>{item.description} ({item.articleNumber}) · {item.supplier} · quote ${line.unitPrice.toFixed(2)} / unit · catalog {item.currency} {item.unitPrice} / {item.packQuantity} {item.unit} · {item.match}</p>
                          {item.sameSupplier && item.match === 'exact' && Number(item.packQuantity) === 1 && item.currency === 'USD' && (
                            <button type="button" className="mt-1 font-medium text-accent-deep" onClick={() => applyNegotiatedPrice(index, item)}>
                              Use negotiated unit price
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Total Cost */}
        <div className="bg-accent-soft/20 border-2 border-accent/40 rounded-lg p-6">
          <div className="flex items-center justify-between">
            <span className="text-lg font-medium text-gray-900">Total Cost</span>
            <Input label="Quoted total ($), including shipping and tax" type="number" step="0.01" min="0" value={totalCost} onChange={(e) => { setTotalCost(Number(e.target.value)); setPdfReviewed(false); setUnreviewedValues((current) => current.filter((key) => key !== 'totalCost')); }} required />
          </div>
        </div>

        {/* Submit */}
        {file && (
          <label className="flex items-start gap-3 text-sm text-ink">
            <input type="checkbox" checked={pdfReviewed} onChange={(e) => setPdfReviewed(e.target.checked)} className="mt-1" />
            I compared the supplier, selected items and quoted total with the PDF and corrected any extraction errors.
          </label>
        )}
        <div className="flex items-center justify-end space-x-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => router.back()}
          >
            Cancel
          </Button>
          <Button type="submit" size="lg" disabled={submitting || extracting}>
            Create Request
          </Button>
        </div>
      </form>
    </div>
  );
}

